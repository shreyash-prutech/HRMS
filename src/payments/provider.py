from abc import ABC, abstractmethod
from typing import Any


class PaymentProvider(ABC):
    @abstractmethod
    def authorize(self, request: Any, context: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def capture(self, request: Any, context: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def void(self, request: Any, context: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def refund(self, request: Any, context: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def verify_webhook_signature(self, payload: Any, headers: Any, context: Any) -> Any:
        raise NotImplementedError


__all__ = ["PaymentProvider"]
