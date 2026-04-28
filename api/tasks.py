import logging
import random

from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone

from api.v1.services.payout_service import InvalidPayoutState, PayoutService

logger = logging.getLogger(__name__)

MAX_PAYOUT_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30


@shared_task
def cleanup_expired_idempotency():
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM api_idempotency WHERE expires_at < NOW()")
        deleted = cursor.rowcount
    logger.info(f"Cleaned up {deleted} expired idempotency keys")

@shared_task(bind=True, max_retries=MAX_PAYOUT_ATTEMPTS - 1)
def process_payout(self, payout_id):
    logger.info("Worker picked up payout %s", payout_id)

    with transaction.atomic():
        with connection.cursor() as cursor:
            # SELECT FOR UPDATE payout
            lock_query = "SELECT merchant_id, amount_paise, status FROM api_payout WHERE id = %s FOR UPDATE"
            cursor.execute(lock_query, [payout_id])
            row = cursor.fetchone()
            
            if not row:
                return f"Payout {payout_id} not found"
            
            merchant_id, amount_paise, current_status = row
            
            if current_status != 'pending':
                return f"Payout {payout_id} not pending (current: {current_status})"
            
            # Set status = processing
            cursor.execute(
                "UPDATE api_payout SET status = 'processing', updated_at = %s WHERE id = %s",
                [timezone.now(), payout_id]
            )
            
            # Simulate Bank
            result = random.choice(['success', 'failure', 'stuck'])
            
            if result == 'success':
                # Set payout = completed
                cursor.execute(
                    "UPDATE api_payout SET status = 'completed', updated_at = %s WHERE id = %s",
                    [timezone.now(), payout_id]
                )
                # Mark ledger debit = completed
                cursor.execute(
                    "UPDATE api_ledger SET status = 'completed' WHERE payout_id = %s AND type = 'debit'",
                    [payout_id]
                )
                logger.info("Payout %s completed successfully", payout_id)
                return f"Payout {payout_id} completed"
                
            elif result == 'failure':
                # Set payout = failed
                cursor.execute(
                    "UPDATE api_payout SET status = 'failed', updated_at = %s WHERE id = %s",
                    [timezone.now(), payout_id]
                )
                # Mark ledger debit = completed (it was held, now we mark it completed to close the hold logic)
                cursor.execute(
                    "UPDATE api_ledger SET status = 'completed' WHERE payout_id = %s AND type = 'debit'",
                    [payout_id]
                )
                # Insert reversal credit
                cursor.execute(
                    """
                    INSERT INTO api_ledger (merchant_id, payout_id, type, amount_paise, status, created_at)
                    VALUES (%s, %s, 'credit', %s, 'completed', %s)
                    """,
                    [merchant_id, payout_id, amount_paise, timezone.now()]
                )
                logger.info("Payout %s failed and was reversed", payout_id)
                return f"Payout {payout_id} failed"
                
            elif result == 'stuck':
                logger.info("Payout %s stuck, retrying later", payout_id)
                raise self.retry(countdown=60)


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

