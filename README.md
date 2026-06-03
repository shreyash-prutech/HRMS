# HRMS
test repo for HRMS

## Project note
This project includes a one-page guest checkout experience for cart, address, payment, and review on a single scrollable page. The checkout uses `AddressLookup` for address autocomplete, emits `checkout_started` and `checkout_completed` analytics events, and submits orders via `POST /api/v2/orders` with `client_request_id` for idempotency.
