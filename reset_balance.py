import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from api.models import Merchant, Ledger
from django.db import transaction

def reset_balance():
    m = Merchant.objects.get_or_create(id=1, defaults={'name': 'Playto Merchant'})[0]
    with transaction.atomic():
        Ledger.objects.filter(merchant=m).delete()
        Ledger.objects.create(
            merchant=m,
            type='credit',
            amount_paise=1000000, # 10,000.00
            status='completed'
        )
    print("Balance successfully reset to 10,000.00 (1,000,000 paise)")

if __name__ == "__main__":
    reset_balance()
