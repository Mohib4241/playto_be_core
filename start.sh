#!/bin/sh

# 1. Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# 2. Start Celery worker in the background
# We use --concurrency=1 to save memory
echo "Starting Celery Worker..."
celery -A payout_engine worker --loglevel=info -Q payouts,celery --concurrency=1 &

# 3. Start Django development server (Lightest memory usage)
echo "Starting Django Server..."
python manage.py runserver 0.0.0.0:$PORT
