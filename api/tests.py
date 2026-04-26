import hashlib
import json
import threading
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, connections
from rest_framework.test import APIClient
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from api.models import Idempotency, Ledger, Merchant, Payout
from celery.exceptions import Retry

from api.tasks import process_payout
from api.v1.services.payout_service import (
    IDEMPOTENCY_TTL_HOURS,
    IdempotencyConflict,
    IdempotencyInProgress,
    InvalidPayoutState,
    LedgerService,
    PayoutService,
)


def request_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class PayoutBaseTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.merchant = Merchant.objects.create(name='Playto Test Merchant')
        Ledger.objects.create(
            merchant=self.merchant,
            type='credit',
            amount_paise=100000,
            status='completed',
        )

    def build_payload(self, **overrides):
        payload = {
            'merchant_id': self.merchant.id,
            'amount_paise': 25000,
            'bank_account_id': 'ACC-1',
        }
        payload.update(overrides)
        return payload


class IdempotencyTests(PayoutBaseTestCase):
    def test_dashboard_endpoint_returns_recent_payouts_payload(self):
        response = self.client.get(f'/api/v1/merchants/{self.merchant.id}/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('recent_payouts', response.data)

    def test_invalid_bank_account_id_is_rejected(self):
        response = self.client.post(
            '/api/v1/payouts/',
            {
                'merchant_id': self.merchant.id,
                'amount_paise': 25000,
                'bank_account_id': ',.,.1321,.,.',
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='idem-invalid-bank-account',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('bank_account_id', response.data['error'])
        self.assertEqual(Payout.objects.count(), 0)

    def test_same_idempotency_key_returns_cached_response(self):
        payload = self.build_payload()

        first_response, first_cached = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'idem-key-1',
            request_hash(payload),
        )
        second_response, second_cached = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'idem-key-1',
            request_hash(payload),
        )

        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual(first_response, second_response)
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(Ledger.objects.filter(type='debit').count(), 1)
        self.assertEqual(first_response['bank_account_id'], payload['bank_account_id'])

    def test_same_key_is_scoped_per_merchant(self):
        other_merchant = Merchant.objects.create(name='Other Merchant')
        Ledger.objects.create(
            merchant=other_merchant,
            type='credit',
            amount_paise=100000,
            status='completed',
        )
        payload = self.build_payload()
        other_payload = {
            **payload,
            'merchant_id': other_merchant.id,
        }

        first_response, _ = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'shared-key',
            request_hash(payload),
        )
        second_response, second_cached = PayoutService.request_payout(
            other_merchant,
            other_payload['amount_paise'],
            other_payload['bank_account_id'],
            'shared-key',
            request_hash(other_payload),
        )

        self.assertFalse(second_cached)
        self.assertNotEqual(first_response['id'], second_response['id'])
        self.assertEqual(Payout.objects.count(), 2)

    def test_reusing_key_with_different_payload_is_rejected(self):
        payload = self.build_payload()
        PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'idem-key-2',
            request_hash(payload),
        )

        conflicting_payload = self.build_payload(amount_paise=30000)
        with self.assertRaises(IdempotencyConflict):
            PayoutService.request_payout(
                self.merchant,
                conflicting_payload['amount_paise'],
                conflicting_payload['bank_account_id'],
                'idem-key-2',
                request_hash(conflicting_payload),
            )

    def test_in_progress_idempotency_key_is_rejected(self):
        payload = self.build_payload()
        Idempotency.objects.create(
            merchant=self.merchant,
            key='idem-key-in-flight',
            request_hash=request_hash(payload),
            status='in_progress',
        )

        with self.assertRaises(IdempotencyInProgress):
            PayoutService.request_payout(
                self.merchant,
                payload['amount_paise'],
                payload['bank_account_id'],
                'idem-key-in-flight',
                request_hash(payload),
            )

    def test_expired_idempotency_key_can_be_reused_after_24_hours(self):
        payload = self.build_payload()
        stale_record = Idempotency.objects.create(
            merchant=self.merchant,
            key='expired-key',
            request_hash=request_hash(payload),
            response_json={'id': 999},
            status='completed',
        )
        cutoff = timezone.now() - timedelta(hours=IDEMPOTENCY_TTL_HOURS, minutes=1)
        Idempotency.objects.filter(id=stale_record.id).update(
            created_at=cutoff,
            updated_at=cutoff,
        )

        response, from_cache = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'expired-key',
            request_hash(payload),
        )

        self.assertFalse(from_cache)
        self.assertNotEqual(response['id'], 999)
        self.assertEqual(
            Idempotency.objects.filter(merchant=self.merchant, key='expired-key').count(),
            1,
        )


class BalanceInvariantTests(PayoutBaseTestCase):
    def test_available_balance_matches_credit_minus_debit_ledger(self):
        payload = self.build_payload(amount_paise=40000)
        response, _ = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'balance-key',
            request_hash(payload),
        )
        with patch('api.tasks.random.randint', return_value=10):
            process_payout(response['id'])

        credits = Ledger.objects.filter(
            merchant=self.merchant,
            type='credit',
            status='completed',
        )
        debits = Ledger.objects.filter(
            merchant=self.merchant,
            type='debit',
        )
        expected_balance = sum(entry.amount_paise for entry in credits) - sum(
            entry.amount_paise for entry in debits
        )
        self.assertEqual(expected_balance, LedgerService.get_balance(self.merchant.id))


class StateMachineTests(PayoutBaseTestCase):
    def test_failed_payout_returns_funds_atomically(self):
        payload = self.build_payload(amount_paise=25000)
        response, _ = PayoutService.request_payout(
            self.merchant,
            payload['amount_paise'],
            payload['bank_account_id'],
            'state-key',
            request_hash(payload),
        )

        with patch('api.tasks.random.randint', return_value=80):
            process_payout(response['id'])

        payout = Payout.objects.get(id=response['id'])
        self.assertEqual(payout.status, 'failed')
        self.assertEqual(LedgerService.get_balance(self.merchant.id), 100000)
        self.assertEqual(
            Ledger.objects.filter(
                payout_id=response['id'],
                type='credit',
                status='completed',
            ).count(),
            1,
        )

    def test_illegal_terminal_transition_is_rejected(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=1000,
            bank_account_id='ACC-TERM',
            status='completed',
        )

        with self.assertRaises(InvalidPayoutState):
            process_payout(payout.id)


class RetryLogicTests(PayoutBaseTestCase):
    def test_processing_payout_retries_with_exponential_backoff(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=25000,
            bank_account_id='ACC-RETRY',
            status='pending',
            attempts=0,
        )
        Ledger.objects.create(
            merchant=self.merchant,
            payout=payout,
            type='debit',
            amount_paise=25000,
            status='pending',
        )

        with patch('api.tasks.random.randint', return_value=95):
            with self.assertRaises(Retry):
                process_payout(payout.id)

        payout.refresh_from_db()
        self.assertEqual(payout.status, 'processing')
        self.assertEqual(payout.attempts, 1)

    def test_processing_payout_fails_after_third_attempt_and_refunds(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=25000,
            bank_account_id='ACC-RETRY-FAIL',
            status='processing',
            attempts=3,
            processing_started_at=timezone.now() - timedelta(seconds=31),
        )
        Ledger.objects.create(
            merchant=self.merchant,
            payout=payout,
            type='debit',
            amount_paise=25000,
            status='pending',
        )

        message = process_payout(payout.id)

        payout.refresh_from_db()
        self.assertEqual(payout.status, 'failed')
        self.assertIn('exhausted retries', message)
        self.assertEqual(LedgerService.get_balance(self.merchant.id), 100000)

    @patch('api.v1.views.payout_views.process_payout.delay')
    def test_retry_endpoint_creates_new_pending_payout_for_failed_payout(self, delay_mock):
        failed_payout = Payout.objects.create(
            merchant=self.merchant,
            amount_paise=25000,
            bank_account_id='ACC-RETRY01',
            status='failed',
        )

        response = self.client.post(f'/api/v1/payouts/{failed_payout.id}/retry/')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Payout.objects.count(), 2)
        self.assertEqual(response.data['bank_account_id'], failed_payout.bank_account_id)
        delay_mock.assert_called_once_with(response.data['id'])


class ConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.merchant = Merchant.objects.create(name='Concurrent Merchant')
        Ledger.objects.create(
            merchant=self.merchant,
            type='credit',
            amount_paise=10000,
            status='completed',
        )

    def test_concurrent_payouts_cannot_overdraw_balance(self):
        if connection.vendor != 'postgresql':
            self.skipTest('This concurrency test requires PostgreSQL row-level locks.')

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def request_payout(index):
            connections.close_all()
            payload = {
                'merchant_id': self.merchant.id,
                'amount_paise': 6000,
                'bank_account_id': f'ACC-CONCURRENT-{index}',
            }
            barrier.wait()
            try:
                response, _ = PayoutService.request_payout(
                    self.merchant,
                    payload['amount_paise'],
                    payload['bank_account_id'],
                    f'concurrent-key-{index}',
                    request_hash(payload),
                )
                results.append(response)
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=request_payout, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(errors, ['Insufficient balance'])
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(LedgerService.get_balance(self.merchant.id), 4000)

    def test_concurrent_same_idempotency_key_creates_only_one_payout(self):
        if connection.vendor != 'postgresql':
            self.skipTest('This same-key concurrency test requires PostgreSQL unique-index locking.')

        barrier = threading.Barrier(2)
        results = []

        def request_with_same_key():
            connections.close_all()
            payload = {
                'merchant_id': self.merchant.id,
                'amount_paise': 4000,
                'bank_account_id': 'ACC-IDEM-SAME',
            }
            barrier.wait()
            response, from_cache = PayoutService.request_payout(
                self.merchant,
                payload['amount_paise'],
                payload['bank_account_id'],
                'shared-idem-key',
                request_hash(payload),
            )
            results.append((response, from_cache))
            connections.close_all()

        threads = [threading.Thread(target=request_with_same_key) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(sum(1 for _, from_cache in results if from_cache), 1)
