import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from django.db import connection
from api.models import Merchant, Payout, Ledger, Idempotency
from django.conf import settings

def reset_database():
    # 1. Clear Message Broker (RabbitMQ)
    print(f"🧹 Clearing Message Broker ({settings.CELERY_BROKER_URL.split('://')[0]})...")
    try:
        from celery import Celery
        app = Celery('payout_engine')
        app.config_from_object('django.conf:settings', namespace='CELERY')
        
        # Purge standard queues
        with app.connection_or_acquire() as conn:
            conn.default_channel.queue_purge('payouts')
            conn.default_channel.queue_purge('celery')
        print("  - RabbitMQ queues purged successfully.")
    except Exception as e:
        print(f"  - Warning: Could not purge queues: {e} (This is normal if queues are already empty)")

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

    print("\n✅ System is now FRESH (Database + Broker).")

if __name__ == "__main__":
    reset_database()
