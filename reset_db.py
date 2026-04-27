import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency

import redis
from django.conf import settings

def reset_database():
    # 1. Clear Redis
    print("🧹 Clearing Redis cloud cache...")
    try:
        r = redis.from_url(settings.CELERY_BROKER_URL)
        r.flushall()
        print("  - Redis cleared successfully.")
    except Exception as e:
        print(f"  - Warning: Could not clear Redis: {e}")

    # 2. Clear Database
    print("🧹 Clearing database history (ULTRA FAST)...")
    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE api_idempotency RESTART IDENTITY CASCADE;")
        cursor.execute("TRUNCATE TABLE api_ledger RESTART IDENTITY CASCADE;")
        cursor.execute("TRUNCATE TABLE api_payout RESTART IDENTITY CASCADE;")
    
    # 3. Restore initial balances for all merchants
    print("💰 Restoring merchant balances to ₹10,000.00...")
    for merchant in Merchant.objects.all():
        Ledger.objects.create(
            merchant=merchant,
            type='credit',
            amount_paise=1000000, # ₹10,000.00
            status='completed'
        )
        print(f"  - Reset balance for: {merchant.name}")

    print("\n✅ System is now FRESH (Database + Redis).")

if __name__ == "__main__":
    reset_database()
