import redis
from django.conf import settings
from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency

class SystemService:
    @staticmethod
    def reset_system():
        # 1. Clear Redis
        try:
            r = redis.from_url(settings.CELERY_BROKER_URL)
            r.flushall()
        except Exception as e:
            # We log but don't fail the whole process if Redis is unreachable
            print(f"Warning: Could not clear Redis: {e}")

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
