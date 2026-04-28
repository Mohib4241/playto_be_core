import logging
import hmac
import hashlib
import json
import requests
from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone

from api.v1.services.payout_service import InvalidPayoutState, PayoutService
from api.models import WebhookConfig, WebhookDelivery, Payout

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
            lock_query = "SELECT merchant_id, amount_paise, status, attempts FROM api_payout WHERE id = %s FOR UPDATE"
            cursor.execute(lock_query, [payout_id])
            row = cursor.fetchone()
            
            if not row:
                return f"Payout {payout_id} not found"
            
            merchant_id, amount_paise, current_status, attempts = row
            
            if current_status != 'pending':
                return f"Payout {payout_id} not pending (current: {current_status})"
            
            # 2. Max Attempts Check
            if attempts >= MAX_PAYOUT_ATTEMPTS:
                # Move to failed and return funds
                cursor.execute(
                    "UPDATE api_payout SET status = 'failed', updated_at = %s WHERE id = %s",
                    [timezone.now(), payout_id]
                )
                cursor.execute(
                    "UPDATE api_ledger SET status = 'completed' WHERE payout_id = %s AND type = 'debit'",
                    [payout_id]
                )
                cursor.execute(
                    """
                    INSERT INTO api_ledger (merchant_id, payout_id, type, amount_paise, status, created_at)
                    VALUES (%s, %s, 'credit', %s, 'completed', %s)
                    """,
                    [merchant_id, payout_id, amount_paise, timezone.now()]
                )
                logger.error("Payout %s reached max attempts and was failed", payout_id)
                return f"Payout {payout_id} failed after {attempts} attempts"

            # 3. Set status = processing and increment attempts
            cursor.execute(
                "UPDATE api_payout SET status = 'processing', attempts = attempts + 1, updated_at = %s WHERE id = %s",
                [timezone.now(), payout_id]
            )
            
            # Simulate Bank (Hardcoded to 100% success for speed and reliability)
            result = 'success'
            
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
                send_webhook_event.delay(payout_id, 'payout.completed')
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
                send_webhook_event.delay(payout_id, 'payout.failed')
                return f"Payout {payout_id} failed"
                
            elif result == 'stuck':
                logger.info("Payout %s stuck, retrying later", payout_id)
                # Exponential backoff: 30s, 60s, 120s...
                backoff = RETRY_BASE_SECONDS * (2 ** self.request.retries)
                # Move back to pending so reconciliation or retry can pick it up
                cursor.execute("UPDATE api_payout SET status = 'pending' WHERE id = %s", [payout_id])
                raise self.retry(countdown=backoff)


@shared_task
def reconcile_pending_payouts():
    # 1. Payouts stuck in 'pending' for > 2m
    # 2. Payouts stuck in 'processing' for > 30s
    cutoff_pending = timezone.now() - timezone.timedelta(minutes=2)
    cutoff_processing = timezone.now() - timezone.timedelta(seconds=30)
    
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM api_payout 
            WHERE (status = 'pending' AND created_at < %s)
               OR (status = 'processing' AND updated_at < %s)
            """,
            [cutoff_pending, cutoff_processing]
        )
        stale_payouts = cursor.fetchall()
    
    if not stale_payouts:
        return "No stale payouts found"

    count = 0
    for (payout_id,) in stale_payouts:
        # Move back to pending so process_payout can lock it
        with connection.cursor() as cursor:
            cursor.execute("UPDATE api_payout SET status = 'pending' WHERE id = %s", [payout_id])
        
        # Re-enqueue the task
        process_payout.apply_async(args=[payout_id], queue="payouts")
        count += 1
    
    logger.info("Reconciled %s stale payouts", count)
    return f"Reconciled {count} payouts"


@shared_task
def send_webhook_event(payout_id, event_type):
    """
    Find all active webhooks for the merchant and create delivery records.
    """
    try:
        payout = Payout.objects.select_related('merchant').get(id=payout_id)
    except Payout.DoesNotExist:
        return f"Payout {payout_id} not found"

    configs = WebhookConfig.objects.filter(merchant=payout.merchant, is_active=True)
    if not configs.exists():
        return f"No active webhooks for merchant {payout.merchant_id}"

    payload = {
        "event": event_type,
        "data": {
            "payout_id": payout.id,
            "amount_paise": payout.amount_paise,
            "status": payout.status,
            "bank_account_id": payout.bank_account_id,
            "created_at": payout.created_at.isoformat(),
        }
    }

    deliveries = []
    for config in configs:
        delivery = WebhookDelivery.objects.create(
            merchant=payout.merchant,
            payout=payout,
            event_type=event_type,
            payload=payload,
            status='pending'
        )
        dispatch_webhook_delivery.delay(delivery.id)
        deliveries.append(delivery.id)

    return f"Created {len(deliveries)} webhook deliveries"


@shared_task(bind=True, max_retries=5)
def dispatch_webhook_delivery(self, delivery_id):
    """
    Actually send the webhook with signing and retries.
    """
    try:
        delivery = WebhookDelivery.objects.get(id=delivery_id)
        config = WebhookConfig.objects.get(merchant=delivery.merchant, is_active=True)
    except (WebhookDelivery.DoesNotExist, WebhookConfig.DoesNotExist):
        return f"Delivery {delivery_id} or active config not found"

    payload_bytes = json.dumps(delivery.payload).encode('utf-8')
    signature = hmac.new(
        config.secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Signature': signature,
        'X-Webhook-Event': delivery.event_type,
    }

    try:
        delivery.attempts += 1
        delivery.last_attempt_at = timezone.now()
        
        response = requests.post(
            config.url,
            data=payload_bytes,
            headers=headers,
            timeout=10
        )
        
        delivery.response_code = response.status_code
        delivery.response_body = response.text[:1000] # Limit size
        
        if 200 <= response.status_code < 300:
            delivery.status = 'sent'
            delivery.save()
            return f"Webhook {delivery_id} sent successfully"
        else:
            delivery.status = 'failed'
            delivery.save()
            # Exponential backoff retry
            raise self.retry(countdown=60 * (2 ** self.request.retries))
            
    except Exception as e:
        delivery.status = 'failed'
        delivery.response_body = str(e)
        delivery.save()
        # Exponential backoff retry
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))

