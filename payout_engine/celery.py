import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')

# Extra safeguard: Force RPC backend if amqp is detected to prevent ModuleNotFoundError
_env_backend = os.environ.get('CELERY_RESULT_BACKEND', '').strip("'\"")
if _env_backend.startswith('amqp') or _env_backend.startswith('redis'):
    os.environ['CELERY_RESULT_BACKEND'] = 'rpc'

app = Celery('payout_engine')
app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
