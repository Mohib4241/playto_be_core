# 🚀 Playto Payout Engine: Architecture & Reliability Guide

This document explains the production-grade reliability features implemented in the Playto Payout Engine to ensure **Zero Duplicate Payments** and **Automated Recovery**.

---

## 🏗️ 1. Core Architecture
The system consists of four main components working in harmony:
1.  **Django API**: Handles incoming payout requests, performs initial validation, and creates the record.
2.  **PostgreSQL (Atomic DB)**: The source of truth. Every state transition is guarded by atomic transactions.
3.  **Celery Workers**: Distributed processing units that handle the actual "bank transfer" simulation.
4.  **Message Broker (CloudAMQP)**: The resilient bridge between the API and the Workers.

---

## 🛡️ 2. Reliability Features

### A. Idempotency (The "Deduplicator")
*   **Feature**: Ensures that if a merchant sends the same request twice (due to network retry), we only pay out once.
*   **Logic**: Uses an `Idempotency-Key` header. We store this key in **Redis** (for speed) and **PostgreSQL** (for persistence).
*   **Result**: 1,000 identical requests will result in exactly **1 successful payment**.

### B. Atomic State Machine (The "Lock")
*   **Feature**: Prevents two workers from accidentally picking up the same payout at the same time.
*   **Logic**: Uses PostgreSQL `SELECT FOR UPDATE`. When a worker picks up a payout, it "locks" that row in the database.
*   **States**: `pending` → `processing` → `completed` OR `failed`.

### C. The Watchman (Reconciliation Task)
*   **Feature**: Automatically fixes payouts that get "stuck" due to worker crashes or network timeouts.
*   **Logic**: A background task runs every 60 seconds. It looks for payouts that have been in `processing` for too long (e.g., > 30s) or stuck in `pending` (> 2m).
*   **Atomic Claim**: It uses an atomic `UPDATE ... RETURNING id` query to "rescue" these tasks and put them back in the queue without interrupting active workers.

### D. Ledger-First Integrity
*   **Feature**: Guarantees that a merchant never spends more money than they have.
*   **Logic**: Money is "held" (debited with status `pending`) *before* the payout starts. If the payout fails, a "reversal credit" is automatically generated.

---

## 🐳 3. Docker Deployment Guide

The system is fully containerized for easy deployment.

### Services in Docker
1.  **`web`**: The Django REST API (Gunicorn in production).
2.  **`worker`**: The Celery process that handles the payouts.
3.  **`beat`**: The scheduler that wakes up the "Watchman."
4.  **`redis`**: The high-speed cache for idempotency.

### How to Run (Local Docker)
```bash
# 1. Build the images
docker-compose build

# 2. Start the whole system
docker-compose up -d

# 3. Monitor logs
docker-compose logs -f worker
```

### Queue Isolation (`payouts_v2`)
In Docker and production, we use a versioned queue name (`payouts_v2`). This ensures that if you deploy a new version of the code, old "ghost" workers on other servers won't steal your tasks and crash them.

---

## 📊 4. Monitoring & Testing

### Real-time Monitoring
Run the custom monitor script to see your queue health:
```bash
python check_queues.py
```

### Stress Testing
Use the provided script to blast the system with high concurrency:
```bash
python stress_test.py
```
*The system is tuned to handle ~2,000 requests per second at the API level and process them reliably through the worker.*

---

## 🧹 5. The "Fresh Start" (Reset)
If you need to wipe everything for a new test, use the **System Reset** button in the UI or call the API:
`POST /api/v1/system/reset/`

**This will:**
1. Flush **Redis** (clear cache).
2. Purge **RabbitMQ** (clear queues).
3. Truncate **Postgres** (reset all IDs to 1 and balances to ₹10,000).
