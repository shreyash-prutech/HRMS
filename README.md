# HRMS
test repo for HRMS

## API usage: `POST /api/v2/orders`

Create an order with an idempotency key:

- `Idempotency-Key` header is required.
- The value must be a UUID v4.
- Replays with the same key within 24 hours return the original response with HTTP 200 and `X-Idempotent-Replay: true`.
- If the same key is reused with a different payload, the API returns HTTP 409 with diff details.
- Idempotency records are stored in `idempotency_records` and retained for 24 hours before cleanup.
