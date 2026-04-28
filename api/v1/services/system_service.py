from django.conf import settings
from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency

class SystemService:
    @staticmethod
    def reset_system():
        # 1. Clear Message Broker (RabbitMQ)
        try:
            from payout_engine.celery import app
            with app.connection_or_acquire() as conn:
                for q_name in ['payouts', 'payouts_v2', 'payouts_retry', 'celery']:
                    try:
                        conn.default_channel.queue_purge(q_name)
                    except Exception:
                        pass
        except Exception as e:
            print(f"Warning: Could not purge queues: {e}")

        # 2. Clear Redis Cache (Idempotency)
        try:
            from django.core.cache import cache
            cache.clear()
        except Exception as e:
            print(f"Warning: Could not clear cache: {e}")

        # 3. Clear Database (Using TRUNCATE for speed and resetting IDs)
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE api_idempotency RESTART IDENTITY CASCADE;")
            cursor.execute("TRUNCATE TABLE api_ledger RESTART IDENTITY CASCADE;")
            cursor.execute("TRUNCATE TABLE api_payout RESTART IDENTITY CASCADE;")
        
        # 4. Restore initial balances for all merchants
        merchants = Merchant.objects.all()
        for merchant in merchants:
            Ledger.objects.create(
                merchant=merchant,
                type='credit',
                amount_paise=1000000, # ₹10,000.00
                status='completed'
            )
        
        return True
