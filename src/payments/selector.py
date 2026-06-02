from __future__ import annotations

from typing import Any, Callable, Mapping, Type

from src.payments.provider import PaymentProvider

ProviderFactory = Type[PaymentProvider]
FeatureFlagAccessor = Callable[[str], bool]

PROVIDER_REGISTRY: Mapping[str, ProviderFactory] = {}


def _normalize_country_code(country_code: Any) -> str:
    if country_code is None:
        return ""
    normalized = str(country_code).strip().upper()
    if normalized in {"INDIA"}:
        return "IN"
    if normalized in {"BRAZIL"}:
        return "BR"
    return normalized


def is_provider_v2_enabled(feature_flag_accessor: FeatureFlagAccessor) -> bool:
    return bool(feature_flag_accessor("payments.provider_v2"))


def get_payment_provider_class(provider_name: str) -> ProviderFactory:
    key = str(provider_name).strip().lower()
    if key not in PROVIDER_REGISTRY:
        raise KeyError(f"Unknown payment provider: {provider_name}")
    return PROVIDER_REGISTRY[key]


def select_payment_provider(country_code: Any, feature_flag_accessor: FeatureFlagAccessor) -> ProviderFactory:
    provider_v2_enabled = is_provider_v2_enabled(feature_flag_accessor)
    normalized_country = _normalize_country_code(country_code)

    if provider_v2_enabled and normalized_country in {"IN", "BR"}:
        provider_name = "adyen"
    else:
        provider_name = "stripe"

    return get_payment_provider_class(provider_name)


__all__ = [
    "FeatureFlagAccessor",
    "PROVIDER_REGISTRY",
    "ProviderFactory",
    "get_payment_provider_class",
    "is_provider_v2_enabled",
    "select_payment_provider",
]
