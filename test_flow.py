import requests
import time
import uuid

API_BASE = "https://playto-server.onrender.com/api/v1"

def test_payout_flow():
    # 1. Get Dashboard
    print("Fetching Dashboard...")
    res = requests.get(f"{API_BASE}/merchants/1/dashboard/")
    if res.status_code != 200:
        print(f"Error fetching dashboard: {res.text}")
        return
    
    data = res.json()
    print(f"Merchant: {data['merchant']['name']}")
    print(f"Current Balance: ₹{data['balance_paise']/100:.2f}")

    # 2. Request Payout
    idem_key = str(uuid.uuid4())
    payload = {
        "amount_paise": 50000, # ₹500
        "bank_account_id": "BANK-TEST-123",
        "merchant_id": 1
    }
    
    print(f"\nRequesting Payout of ₹500.00 (Idempotency-Key: {idem_key})...")
    res = requests.post(f"{API_BASE}/payouts/", json=payload, headers={"Idempotency-Key": idem_key})
    
    if res.status_code not in [200, 201]:
        print(f"Error creating payout: {res.text}")
        return
    
    payout = res.json()
    payout_id = payout['id']
    print(f"Payout Created! ID: {payout_id}, Status: {payout['status']}")

    # 3. Poll for status
    print("\nPolling for status updates...")
    for _ in range(10):
        time.sleep(3)
        res = requests.get(f"{API_BASE}/payouts/{payout_id}/")
        payout = res.json()
        print(f"Status check: {payout['status']}")
        if payout['status'] in ['completed', 'failed']:
            print(f"\nPayout reached final state: {payout['status'].upper()}")
            break
    
    # 4. Final Balance
    print("\nFetching updated Dashboard...")
    res = requests.get(f"{API_BASE}/merchants/1/dashboard/")
    data = res.json()
    print(f"Final Balance: ₹{data['balance_paise']/100:.2f}")

if __name__ == "__main__":
    test_payout_flow()
