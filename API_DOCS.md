# Playto Payout Engine API Documentation

This document provides a Swagger-style overview of the API endpoints available in the Payout Engine. All endpoints are prefixed with `/api/v1/`.

---

## 1. Create a Payout
**`POST /api/v1/payouts/`**

Initiates a new payout transfer. It handles idempotency to prevent duplicate payouts.

### Request Headers
- `Idempotency-Key` (string, **required**): A unique UUID for this transaction to ensure idempotency.

### Request Body (application/json)
```json
{
  "merchant_id": 1,
  "amount_paise": 1000,
  "bank_account_id": "acc_0987654321"
}
```

*Note: Alternatively, if retrying an existing payout, you can pass just `{ "payout_id": 123 }`.*

### Responses

**`201 Created`** (or `200 OK` if cached via idempotency)
```json
{
  "id": 123,
  "amount_paise": 1000,
  "bank_account_id": "acc_0987654321",
  "status": "pending",
  "created_at": "2023-10-01T12:00:00.000Z"
}
```

**`400 Bad Request`**
```json
{
  "error": "amount_paise must be greater than zero"
}
```

**`409 Conflict`** (If the same idempotency key is used with a different payload, or request is already in-progress)
```json
{
  "error": "Idempotency key already used with different payload"
}
```

---

## 2. Get Payout Details
**`GET /api/v1/payouts/{id}/`**

Retrieves the current status and details of a specific payout.

### Path Parameters
- `id` (integer, **required**): The ID of the payout.

### Responses

**`200 OK`**
```json
{
  "id": 123,
  "amount_paise": 1000,
  "bank_account_id": "acc_0987654321",
  "status": "completed",
  "created_at": "2023-10-01T12:00:00.000Z"
}
```

**`404 Not Found`**
```json
{
  "error": "Payout not found"
}
```

---

## 3. Retry a Failed Payout
**`POST /api/v1/payouts/{id}/retry/`**

Retries a previously failed payout.

### Path Parameters
- `id` (integer, **required**): The ID of the payout.

### Responses

**`200 OK`**
```json
{
  "id": 123,
  "amount_paise": 1000,
  "bank_account_id": "acc_0987654321",
  "status": "pending",
  "created_at": "2023-10-01T12:00:00.000Z"
}
```

**`400 Bad Request`**
```json
{
  "error": "Only failed payouts can be retried"
}
```

---

## 4. Merchant Dashboard
**`GET /api/v1/merchants/{id}/dashboard/`**

Retrieves the current balance, held balance, and recent payout history for a specific merchant.

### Path Parameters
- `id` (integer, **required**): The ID of the merchant.

### Query Parameters
- `page` (integer, optional): The page number for recent payouts (default: 1).
- `limit` (integer, optional): The number of payouts per page (default: 10).

### Responses

**`200 OK`**
```json
{
  "merchant": {
    "id": 1,
    "name": "Acme Corp"
  },
  "balance_paise": 500000,
  "held_balance_paise": 10000,
  "total_payouts": 42,
  "page": 1,
  "limit": 10,
  "recent_payouts": [
    {
      "id": 123,
      "amount_paise": 1000,
      "bank_account_id": "acc_0987654321",
      "status": "completed",
      "created_at": "2023-10-01T12:00:00.000Z"
    }
  ]
}
```

**`404 Not Found`**
```json
{
  "error": "Merchant not found"
}
```

---

## 5. System Reset
**`POST /api/v1/system/reset/`**

*Admin Only:* Flushes the database, resets ledgers, cleans up the RabbitMQ queues, and seeds a fresh testing state.

### Responses

**`200 OK`**
```json
{
  "message": "System reset successfully (Database + Broker)"
}
```
