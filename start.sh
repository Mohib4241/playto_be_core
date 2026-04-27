#!/bin/sh

# Decide which process to run based on an environment variable
# Options: 'api', 'worker', or 'both' (default)

if [ "$PROCESS_TYPE" = "worker" ]; then
    echo "Starting ONLY Celery Worker..."
    celery -A payout_engine worker --loglevel=info -Q payouts,celery --concurrency=2

elif [ "$PROCESS_TYPE" = "api" ]; then
    echo "Starting ONLY Django Server..."
    python manage.py runserver 0.0.0.0:$PORT

else
    echo "Starting BOTH API and Worker (Combined Mode)..."
    celery -A payout_engine worker --loglevel=info -Q payouts,celery --concurrency=1 &
    python manage.py runserver 0.0.0.0:$PORT
fi
