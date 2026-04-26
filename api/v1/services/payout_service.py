from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from api.models import Idempotency

IDEMPOTENCY_TTL_HOURS = 24


class IdempotencyConflict(ValueError):
    pass


class IdempotencyInProgress(ValueError):
    pass


class InvalidPayoutState(ValueError):
    pass

class LedgerService:
    @staticmethod
    def get_balance(merchant_id):
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    COALESCE(SUM(CASE WHEN type = 'credit' AND status = 'completed' THEN amount_paise ELSE 0 END), 0) -
                    COALESCE(SUM(CASE WHEN type = 'debit' THEN amount_paise ELSE 0 END), 0) as balance
                FROM api_ledger
                WHERE merchant_id = %s
            """
            cursor.execute(query, [merchant_id])
            row = cursor.fetchone()
            return row[0] if row else 0

    @staticmethod
    def create_hold(merchant_id, payout_id, amount_paise):
        """Create a pending debit HOLD linked directly to the payout by payout_id."""
        with connection.cursor() as cursor:
            query = """
                INSERT INTO api_ledger (merchant_id, payout_id, type, amount_paise, status, created_at)
                VALUES (%s, %s, 'debit', %s, 'pending', %s)
                RETURNING id
            """
            cursor.execute(query, [merchant_id, payout_id, amount_paise, timezone.now()])
            return cursor.fetchone()[0]


class PayoutService:
    @staticmethod
    def get_balance(merchant_id):
        return LedgerService.get_balance(merchant_id)

    @staticmethod
    def get_idempotency_cutoff():
        return timezone.now() - timedelta(hours=IDEMPOTENCY_TTL_HOURS)

    @staticmethod
    def _delete_expired_idempotency_records(merchant_id, key):
        cutoff = PayoutService.get_idempotency_cutoff()
        Idempotency.objects.filter(
            merchant_id=merchant_id,
            key=key,
            created_at__lt=cutoff,
        ).delete()

    @staticmethod
    def mark_payout_failed(payout_id):
        with transaction.atomic():
            with connection.cursor() as cursor:
                lock_query = """
                    SELECT merchant_id, amount_paise, status
                    FROM api_payout
                    WHERE id = %s
                """
                if connection.features.has_select_for_update:
                    lock_query += " FOR UPDATE"
                cursor.execute(lock_query, [payout_id])
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Payout {payout_id} not found")

                merchant_id, amount_paise, current_status = row
                if current_status != 'processing':
                    raise InvalidPayoutState(
                        f"Cannot transition payout {payout_id} from {current_status} to failed"
                    )

                cursor.execute(
                    """
                    UPDATE api_payout
                    SET status = 'failed',
                        next_retry_at = NULL,
                        updated_at = %s
                    WHERE id = %s AND status = 'processing'
                    """,
                    [timezone.now(), payout_id],
                )
                if cursor.rowcount == 0:
                    raise InvalidPayoutState(f"Failed to mark payout {payout_id} as failed")

                cursor.execute(
                    """
                    UPDATE api_ledger
                    SET status = 'completed'
                    WHERE payout_id = %s AND type = 'debit' AND status = 'pending'
                    """,
                    [payout_id],
                )
                if cursor.rowcount > 0:
                    cursor.execute(
                        """
                        INSERT INTO api_ledger (merchant_id, payout_id, type, amount_paise, status, created_at)
                        VALUES (%s, %s, 'credit', %s, 'completed', %s)
                        """,
                        [merchant_id, payout_id, amount_paise, timezone.now()],
                    )

    @staticmethod
    def retry_payout(payout_id):
        with transaction.atomic():
            with connection.cursor() as cursor:
                # 1. Lock the payout
                lock_query = """
                    SELECT merchant_id, amount_paise, status
                    FROM api_payout
                    WHERE id = %s
                """
                if connection.features.has_select_for_update:
                    lock_query += " FOR UPDATE"
                cursor.execute(lock_query, [payout_id])
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Payout {payout_id} not found")

                merchant_id, amount_paise, current_status = row
                if current_status != 'failed':
                    raise InvalidPayoutState(f"Cannot retry payout {payout_id} in state {current_status}")

                # 2. Check balance (funds were returned on failure)
                balance = LedgerService.get_balance(merchant_id)
                if balance < amount_paise:
                    raise ValueError("Insufficient balance to retry payout")

                # 3. Reset payout state
                cursor.execute(
                    """
                    UPDATE api_payout
                    SET status = 'pending',
                        attempts = 0,
                        next_retry_at = NULL,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    [timezone.now(), payout_id]
                )

                # 4. Create a new HOLD (the old one was reversed)
                LedgerService.create_hold(merchant_id, payout_id, amount_paise)

                return {
                    "id": payout_id,
                    "amount_paise": amount_paise,
                    "status": "pending",
                    "updated_at": timezone.now().isoformat()
                }

    @staticmethod
    def request_payout(merchant, amount_paise, bank_account_id, idempotency_key, request_hash):
        with transaction.atomic():
            PayoutService._delete_expired_idempotency_records(merchant.id, idempotency_key)
            # 1. Claim the idempotency key. A concurrent request with the same
            # key waits on the database unique constraint until this transaction
            # commits, then reads the stored response.
            try:
                with transaction.atomic():
                    idempotency = Idempotency.objects.create(
                        merchant_id=merchant.id,
                        key=idempotency_key,
                        request_hash=request_hash,
                        status='in_progress',
                    )
            except IntegrityError:
                idempotency = (
                    Idempotency.objects.select_for_update()
                    .get(merchant_id=merchant.id, key=idempotency_key)
                )
                if idempotency.created_at < PayoutService.get_idempotency_cutoff():
                    raise IdempotencyConflict("Expired idempotency key is being replaced concurrently")
                if idempotency.request_hash != request_hash:
                    raise IdempotencyConflict("Idempotency-Key was reused with a different request body")
                if idempotency.status == 'in_progress':
                    raise IdempotencyInProgress("A request with this Idempotency-Key is still in progress")
                return idempotency.response_json, True

            with connection.cursor() as cursor:
                # 2. Row-level lock on merchant — prevents double-spend
                lock_query = "SELECT id FROM api_merchant WHERE id = %s"
                if connection.features.has_select_for_update:
                    lock_query += " FOR UPDATE"
                cursor.execute(lock_query, [merchant.id])

                # 3. DB-level balance check
                balance = LedgerService.get_balance(merchant.id)
                if balance < amount_paise:
                    raise ValueError("Insufficient balance")

                # 4. Create payout in pending state
                cursor.execute(
                    """
                    INSERT INTO api_payout (merchant_id, amount_paise, bank_account_id, status, attempts, created_at)
                    VALUES (%s, %s, %s, 'pending', 0, %s)
                    RETURNING id, created_at
                    """,
                    [merchant.id, amount_paise, bank_account_id, timezone.now()]
                )
                payout_id, created_at = cursor.fetchone()

                # 5. Create HOLD linked to this exact payout_id
                LedgerService.create_hold(merchant.id, payout_id, amount_paise)

                response_data = {
                    "id": payout_id,
                    "amount_paise": amount_paise,
                    "bank_account_id": bank_account_id,
                    "status": "pending",
                    "created_at": created_at.isoformat()
                }

                # 6. Complete idempotency record with the first response
                idempotency.response_json = response_data
                idempotency.status = 'completed'
                idempotency.save(update_fields=['response_json', 'status', 'updated_at'])

                return response_data, False
