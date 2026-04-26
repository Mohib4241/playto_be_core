import os
import django
from django.db import connection

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from api.v1.services.payout_service import LedgerService

with connection.cursor() as cursor:
    cursor.execute('SELECT COUNT(*) FROM api_payout')
    print(f'Payout count: {cursor.fetchone()[0]}')
    cursor.execute('SELECT id, name FROM api_merchant')
    print(f'Merchants: {cursor.fetchall()}')
    
    # Check balance for merchant 1
    balance = LedgerService.get_balance(1)
    print(f'Balance for merchant 1: {balance}')
    
    # Also check if there are payouts for merchant 1
    cursor.execute('SELECT COUNT(*) FROM api_payout WHERE merchant_id = 1')
    print(f'Payouts for merchant 1: {cursor.fetchone()[0]}')

    # Check for recent failures/errors in logs or idempotency table
    cursor.execute('SELECT COUNT(*) FROM api_idempotency')
    print(f'Idempotency count: {cursor.fetchone()[0]}')
