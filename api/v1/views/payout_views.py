import hashlib
import json
import re
import uuid
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import connection
from api.v1.services.payout_service import (
    IdempotencyConflict,
    IdempotencyInProgress,
    LedgerService,
    PayoutService,
)
from api.tasks import process_payout

BANK_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


def serialize_payout_row(row):
    return {
        "id": row[0],
        "amount_paise": row[1],
        "bank_account_id": row[2],
        "status": row[3],
        "created_at": row[4].isoformat(),
    }


def fetch_recent_payouts(cursor, merchant_id, limit=10, offset=0):
    cursor.execute(
        """
        SELECT id, amount_paise, bank_account_id, status, created_at
        FROM api_payout
        WHERE merchant_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        [merchant_id, limit, offset],
    )
    return [serialize_payout_row(row) for row in cursor.fetchall()]

def count_total_payouts(cursor, merchant_id):
    cursor.execute("SELECT COUNT(*) FROM api_payout WHERE merchant_id = %s", [merchant_id])
    return cursor.fetchone()[0]

def build_dashboard_payload(cursor, merchant_id, page=1, limit=10):
    offset = (page - 1) * limit
    cursor.execute("SELECT id, name FROM api_merchant WHERE id = %s", [merchant_id])
    merchant_row = cursor.fetchone()
    if not merchant_row:
        return None

    balance = LedgerService.get_balance(merchant_id)
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount_paise), 0)
        FROM api_ledger
        WHERE merchant_id = %s AND type = 'debit' AND status = 'pending'
        """,
        [merchant_id],
    )
    held_balance = cursor.fetchone()[0]

    return {
        "merchant": {"id": merchant_row[0], "name": merchant_row[1]},
        "balance_paise": balance,
        "held_balance_paise": held_balance,
        "total_payouts": count_total_payouts(cursor, merchant_id),
        "page": page,
        "limit": limit,
        "recent_payouts": fetch_recent_payouts(cursor, merchant_id, limit=limit, offset=offset),
    }

class PayoutCreateView(APIView):
    def post(self, request):
        payout_id = request.data.get('payout_id')
        
        # If payout_id is provided, treat it as a retry of an existing payout
        if payout_id:
            try:
                response_data = PayoutService.retry_payout(payout_id)
                process_payout.apply_async(args=[response_data['id']], queue="payouts")
                return Response(response_data, status=status.HTTP_200_OK)
            except (ValueError, InvalidPayoutState) as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        idempotency_key = request.headers.get('Idempotency-Key')
        if not idempotency_key:
            return Response({"error": "Idempotency-Key header is required"}, status=status.HTTP_400_BAD_REQUEST)

        merchant_id = request.data.get('merchant_id', 1)
        amount_paise = request.data.get('amount_paise')
        bank_account_id = request.data.get('bank_account_id')

        if not all([amount_paise, bank_account_id]):
            return Response({"error": "amount_paise and bank_account_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount_paise = int(amount_paise)
        except (TypeError, ValueError):
            return Response({"error": "amount_paise must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        if amount_paise <= 0:
            return Response({"error": "amount_paise must be greater than zero"}, status=status.HTTP_400_BAD_REQUEST)

        bank_account_id = str(bank_account_id).strip()
        if not BANK_ACCOUNT_ID_PATTERN.fullmatch(bank_account_id):
            return Response(
                {"error": "bank_account_id must be 6-40 characters and contain only letters, numbers, hyphen, or underscore"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check merchant exists (Raw SQL)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name FROM api_merchant WHERE id = %s", [merchant_id])
            merchant_row = cursor.fetchone()
            if not merchant_row:
                return Response({"error": "Merchant not found"}, status=status.HTTP_404_NOT_FOUND)
            
            # Create a simple object to pass to service
            class MockMerchant:
                def __init__(self, id, name):
                    self.id = id
                    self.name = name
            merchant = MockMerchant(merchant_row[0], merchant_row[1])

        # Generate request hash
        request_body = json.dumps(request.data, sort_keys=True)
        request_hash = hashlib.sha256(request_body.encode()).hexdigest()

        try:
            response_data, from_cache = PayoutService.request_payout(
                merchant=merchant,
                amount_paise=amount_paise,
                bank_account_id=bank_account_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash
            )

            if not from_cache:
                process_payout.apply_async(args=[response_data['id']], queue="payouts")
                return Response(response_data, status=status.HTTP_201_CREATED)
            else:
                return Response(response_data, status=status.HTTP_200_OK)

        except (IdempotencyConflict, IdempotencyInProgress) as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class PayoutDetailView(APIView):
    def get(self, request, pk):
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, amount_paise, bank_account_id, status, created_at FROM api_payout WHERE id = %s", [pk])
            row = cursor.fetchone()
            if row:
                return Response({
                    "id": row[0],
                    "amount_paise": row[1],
                    "bank_account_id": row[2],
                    "status": row[3],
                    "created_at": row[4].isoformat()
                })
            return Response({"error": "Payout not found"}, status=status.HTTP_404_NOT_FOUND)

class MerchantDashboardView(APIView):
    def get(self, request, pk):
        try:
            page = int(request.query_params.get('page', 1))
            limit = int(request.query_params.get('limit', 10))
        except (ValueError, TypeError):
            page = 1
            limit = 10

        with connection.cursor() as cursor:
            payload = build_dashboard_payload(cursor, pk, page=page, limit=limit)
            if not payload:
                return Response({"error": "Merchant not found"}, status=status.HTTP_404_NOT_FOUND)
            return Response(payload)


class PayoutRetryView(APIView):
    def post(self, request, pk):
        try:
            response_data = PayoutService.retry_payout(pk)
            process_payout.apply_async(args=[response_data['id']], queue="payouts")
            return Response(response_data, status=status.HTTP_200_OK)
        except (ValueError, InvalidPayoutState) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


