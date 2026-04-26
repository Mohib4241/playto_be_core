import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from api.models import Merchant, Ledger

MERCHANTS = [
    (1, 'Arjun Design Studio'),
    (2, 'Priya Freelance Dev'),
    (3, 'Rahul Consulting'),
]


def seed():
    for merchant_id, name in MERCHANTS:
        merchant, created = Merchant.objects.get_or_create(id=merchant_id, defaults={'name': name})
        if not created and merchant.name != name:
            merchant.name = name
            merchant.save(update_fields=['name'])

        print(f"{'Created' if created else 'Ready'} merchant {merchant.id}: {merchant.name}")

        if not Ledger.objects.filter(merchant=merchant).exists():
            Ledger.objects.create(
                merchant=merchant,
                type='credit',
                amount_paise=1000000,
                status='completed'
            )
            print("  Added initial balance of 1,000,000 paise (INR 10,000.00)")

if __name__ == "__main__":
    seed()
