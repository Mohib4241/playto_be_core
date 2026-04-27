from django.conf import settings
from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency

class SystemService:
    @staticmethod
    def reset_system():
        # 1. Clear Message Broker (RabbitMQ/Redis)
        try:
            from celery import Celery
            app = Celery('payout_engine')
            app.config_from_object('django.conf:settings', namespace='CELERY')
            with app.connection_or_acquire() as conn:
                conn.default_channel.queue_purge('payouts')
                conn.default_channel.queue_purge('celery')
        except Exception as e:
            # We log but don't fail the whole process if broker is unreachable
            print(f"Warning: Could not purge queues: {e}")

        # 2. Clear Database (Using TRUNCATE for speed and resetting IDs)
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE api_idempotency RESTART IDENTITY CASCADE;")
            cursor.execute("TRUNCATE TABLE api_ledger RESTART IDENTITY CASCADE;")
            cursor.execute("TRUNCATE TABLE api_payout RESTART IDENTITY CASCADE;")
        
        # 3. Restore initial balances for all merchants
        # Assuming Merchant objects already exist, we just add a credit entry
        merchants = Merchant.objects.all()
        for merchant in merchants:
            Ledger.objects.create(
                merchant=merchant,
                type='credit',
                amount_paise=1000000, # ₹10,000.00
                status='completed'
            )
        
        return True
