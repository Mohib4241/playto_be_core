#!/bin/sh

# Set default port if not provided by Render
PORT="${PORT:-8000}"

# Decide which process to run based on an environment variable
# Options: 'api', 'worker', or 'both' (default)

if [ "$PROCESS_TYPE" = "worker" ]; then
    echo "Starting ONLY Celery Worker..."
    # Render requires a bound port for 'Web Service' types.
    # We start a tiny health check server to satisfy the port scan.
    echo "Starting health check listener on port $PORT..."
    python3 -m http.server "$PORT" &
    
    # Start Celery with solo pool (more efficient for 0.1 CPU / limited memory)
    celery -A payout_engine worker --loglevel=info -Q payouts_v2,payouts_retry,celery -P solo -B

elif [ "$PROCESS_TYPE" = "api" ]; then
    echo "Starting ONLY Django Server..."
    # Use gunicorn for production efficiency
    gunicorn payout_engine.wsgi:application --bind "0.0.0.0:$PORT"

else
    echo "Starting BOTH API and Worker (Combined Mode)..."
    # Start worker in background with solo pool and Beat scheduler
    celery -A payout_engine worker --loglevel=info -Q payouts_v2,payouts_retry,celery -P solo -B &
    
    # Start API
    gunicorn payout_engine.wsgi:application --bind "0.0.0.0:$PORT"
fi
