
import os
import django
from kombu.connection import Connection

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')
django.setup()

from django.conf import settings
from payout_engine.celery import app

def monitor_all_queues():
    print("\n" + "="*40)
    print("🌍 GLOBAL CLOUD-AMQP QUEUE MONITOR")
    print("="*40)
    
    # These are the ones we know about
    known_queues = ['payouts_v2', 'payouts', 'payouts_retry', 'celery']
    
    # Connect and check
    with app.connection_or_acquire() as conn:
        print(f"Connected to: {settings.CELERY_BROKER_URL.split('@')[-1]}") # Show server address safely
        print("-" * 40)
        
        for q_name in known_queues:
            try:
                # passive=True means "only check, don't create"
                q = conn.default_channel.queue_declare(q_name, passive=True)
                print(f"Queue: {q_name:15} | Messages: {q.message_count:5} | Consumers: {q.consumer_count:2}")
            except Exception:
                print(f"Queue: {q_name:15} | Status: NOT ACTIVE")
                
    print("="*40 + "\n")

if __name__ == "__main__":
    monitor_all_queues()
