import logging
import random

from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone

from api.v1.services.payout_service import InvalidPayoutState, PayoutService

logger = logging.getLogger(__name__)

MAX_PAYOUT_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30


def _retry_delay_seconds(attempt_number):
    return RETRY_BASE_SECONDS * (2 ** max(attempt_number - 1, 0))


def _claim_processing_attempt(cursor, payout_id):
    lock_query = """
        SELECT merchant_id, amount_paise, status, attempts
        FROM api_payout
        WHERE id = %s
    """
    if connection.features.has_select_for_update:
        lock_query += " FOR UPDATE"

    cursor.execute(lock_query, [payout_id])
    row = cursor.fetchone()
    if not row:
        return None

    merchant_id, amount_paise, current_status, attempts = row

    if current_status in ('completed', 'failed'):
        raise InvalidPayoutState(
            f"Cannot process payout {payout_id} from terminal state {current_status}"
        )

    if attempts >= MAX_PAYOUT_ATTEMPTS:
        PayoutService.mark_payout_failed(payout_id)
        logger.warning("Payout %s exhausted retries and was marked failed", payout_id)
        return {
            'merchant_id': merchant_id,
            'amount_paise': amount_paise,
            'status': 'failed',
            'attempts': attempts,
            'final_failure': True,
        }

    next_attempt = attempts + 1
    cursor.execute(
        """
        UPDATE api_payout
        SET status = 'processing',
            attempts = %s,
            processing_started_at = %s,
            next_retry_at = NULL,
            updated_at = %s
        WHERE id = %s
        """,
        [next_attempt, timezone.now(), timezone.now(), payout_id],
    )
    logger.info("Payout %s moved to processing on attempt %s", payout_id, next_attempt)
    return {
        'merchant_id': merchant_id,
        'amount_paise': amount_paise,
        'status': 'processing',
        'attempts': next_attempt,
        'claimed': True,
    }


@shared_task(bind=True, max_retries=MAX_PAYOUT_ATTEMPTS - 1)
def process_payout(self, payout_id):
    logger.info("Worker picked up payout %s (Queue: %s)", payout_id, self.request.delivery_info.get('routing_key'))
    # 1. Guard check for terminal status
    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM api_payout WHERE id = %s", [payout_id])
        row = cursor.fetchone()
        if row and row[0] in ('completed', 'failed'):
            logger.info("Payout %s already in terminal state %s, skipping.", payout_id, row[0])
            return f"Payout {payout_id} already {row[0]}"

    with transaction.atomic():
        with connection.cursor() as cursor:
            claim = _claim_processing_attempt(cursor, payout_id)

    if claim is None:
        logger.error("Payout %s not found", payout_id)
        return f"Payout {payout_id} not found"
    if claim.get('final_failure'):
        return f"Payout {payout_id} exhausted retries and failed"

    # 100% Success Mode - No failures designed
    logger.info(
        "Processing payout %s on attempt %s",
        payout_id,
        claim['attempts'],
    )

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_payout
                SET status = 'completed',
                    next_retry_at = NULL,
                    updated_at = %s
                WHERE id = %s AND status = 'processing'
                """,
                [timezone.now(), payout_id],
            )
            if cursor.rowcount == 0:
                raise InvalidPayoutState(f"Cannot mark payout {payout_id} as completed")
            cursor.execute(
                """
                UPDATE api_ledger
                SET status = 'completed'
                WHERE payout_id = %s AND type = 'debit' AND status = 'pending'
                """,
                [payout_id],
            )
    logger.info("Payout %s completed successfully", payout_id)
    return f"Payout {payout_id} completed"


@shared_task
def reconcile_pending_payouts():
    """
    Find payouts stuck in 'pending' for more than 2 minutes and re-enqueue them.
    This ensures that if a worker dies before picking up a task, it eventually gets processed.
    """
    five_minutes_ago = timezone.now() - timezone.timedelta(minutes=2)
    
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM api_payout 
            WHERE status = 'pending' AND created_at < %s
            """,
            [five_minutes_ago]
        )
        stale_payouts = cursor.fetchall()
    
    if not stale_payouts:
        return "No stale payouts found"

    count = 0
    for (payout_id,) in stale_payouts:
        # Re-enqueue the task
        process_payout.apply_async(args=[payout_id], queue="payouts")
        count += 1
    
    logger.info("Reconciled %s stale payouts", count)
    return f"Reconciled {count} payouts"

