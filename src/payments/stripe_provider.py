from __future__ import annotations

from typing import Any

from src.payments.provider import PaymentProvider

try:
    import stripe
except ImportError as exc:  # pragma: no cover
    stripe = None  # type: ignore[assignment]
    _STRIPE_IMPORT_ERROR = exc
else:  # pragma: no cover
    _STRIPE_IMPORT_ERROR = None


class StripePaymentProvider(PaymentProvider):
    def __init__(self) -> None:
        if stripe is None:
            raise ImportError("Stripe SDK is required to use StripePaymentProvider") from _STRIPE_IMPORT_ERROR

    def authorize(self, request: Any, context: Any) -> Any:
        return stripe.PaymentIntent.create(**request)

    def capture(self, request: Any, context: Any) -> Any:
        return stripe.PaymentIntent.capture(request["payment_intent_id"], **request.get("params", {}))

    def void(self, request: Any, context: Any) -> Any:
        return stripe.PaymentIntent.cancel(request["payment_intent_id"], **request.get("params", {}))

    def refund(self, request: Any, context: Any) -> Any:
        return stripe.Refund.create(**request)

    def verify_webhook_signature(self, payload: Any, headers: Any, context: Any) -> Any:
        return stripe.Webhook.construct_event(
            payload,
            headers.get("Stripe-Signature"),
            context["webhook_secret"],
        )


__all__ = ["StripePaymentProvider"]
