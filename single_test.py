import requests
import uuid

API_URL = "http://localhost:8000/api/v1/payouts/"
headers = {
    "Idempotency-Key": str(uuid.uuid4()),
    "Content-Type": "application/json"
}
payload = {
    "merchant_id": 1,
    "amount_paise": 100,
    "bank_account_id": "TEST_ACCOUNT_123"
}

try:
    response = requests.post(API_URL, json=payload, headers=headers)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
