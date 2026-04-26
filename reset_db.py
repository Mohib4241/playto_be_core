import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency

def reset_database():
    print("🧹 Clearing database history (ULTRA FAST)...")
    
    with connection.cursor() as cursor:
        # TRUNCATE is much faster than .delete() for large datasets
        cursor.execute("TRUNCATE TABLE api_idempotency RESTART IDENTITY CASCADE;")
        cursor.execute("TRUNCATE TABLE api_ledger RESTART IDENTITY CASCADE;")
        cursor.execute("TRUNCATE TABLE api_payout RESTART IDENTITY CASCADE;")
    
    # 2. Restore initial balances for all merchants
    print("💰 Restoring merchant balances to ₹10,000.00...")
    for merchant in Merchant.objects.all():
        Ledger.objects.create(
            merchant=merchant,
            type='credit',
            amount_paise=1000000, # ₹10,000.00
            status='completed'
        )
        print(f"  - Reset balance for: {merchant.name}")

    print("\n✅ Database is now FRESH. You can run your stress test!")

if __name__ == "__main__":
    reset_database()
