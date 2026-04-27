# Playto Backend Core (playto_be_core)

A robust, idempotent payout engine built with Django, Celery, and PostgreSQL.

## 🛠 Technical Highlights
- **Ledger Model**: Append-only ledger with immediate debit holds.
- **Concurrency**: PostgreSQL row-level locking (`SELECT FOR UPDATE`).
- **Idempotency**: Atomic database-backed idempotency protection.
- **Worker**: High-throughput Celery workers with separate retry queues.

## 🚀 Running Locally
1. `source venv/bin/activate`
2. `pip install -r requirements.txt`
3. Start RabbitMQ: `docker run -d -p 5672:5672 rabbitmq`
4. `python manage.py runserver`
5. `celery -A payout_engine worker --loglevel=info -Q payouts,payouts_retry,celery`

See [EXPLAINER.md](./EXPLAINER.md) for architectural details.
