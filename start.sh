#!/bin/sh

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Start Gunicorn
echo "Starting Gunicorn..."
gunicorn --bind 0.0.0.0:$PORT payout_engine.wsgi:application
