import asyncio
import aiohttp
import uuid
import time

API_URL = "http://localhost:8000/api/v1/payouts/"
MERCHANT_ID = 1
AMOUNT_PAISE = 100
NUM_REQUESTS = 10000 
CONCURRENCY = 100    # Stable concurrency for cloud-hosted DB

async def send_payout_request(session, idx):
    # Pre-generate headers and payload to save time during the loop
    headers = {
        "Idempotency-Key": str(uuid.uuid4()),
        "Content-Type": "application/json"
    }
    payload = {
        "merchant_id": MERCHANT_ID,
        "amount_paise": AMOUNT_PAISE,
        "bank_account_id": f"ACC_TEST_{idx:06d}"
    }
    
    try:
        async with session.post(API_URL, json=payload, headers=headers) as response:
            return response.status
    except Exception:
        return 500

async def run_stress_test():
    print(f"🔥 Starting ULTRA Stress Test: 100K requests with {CONCURRENCY} concurrency...")
    
    # Use a custom TCPConnector to handle more simultaneous connections
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ttl_dns_cache=300)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        
        async def sem_task(idx):
            async with semaphore:
                return await send_payout_request(session, idx)

        start_time = time.time()
        
        # Create all 100k tasks immediately
        tasks = [sem_task(i) for i in range(NUM_REQUESTS)]
        print(f"✅ Tasks created. Blasting {NUM_REQUESTS} requests now...")
        
        results = await asyncio.gather(*tasks)

        total_duration = time.time() - start_time
        
    status_counts = {}
    for status in results:
        status_counts[status] = status_counts.get(status, 0) + 1

    print("\n" + "="*40)
    print("ULTRA STRESS TEST RESULTS")
    print("="*40)
    print(f"Total Requests: {NUM_REQUESTS}")
    print(f"Total Time: {total_duration:.2f}s")
    print(f"Throughput: {NUM_REQUESTS/total_duration:.2f} req/s")
    print("\nStatus Codes:")
    for status, count in status_counts.items():
        print(f"- {status}: {count} requests")
    print("="*40)

if __name__ == "__main__":
    asyncio.run(run_stress_test())
