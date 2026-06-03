from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import (Depends, FastAPI, Header, HTTPException, Request,
                     Response, status)

try:
    from app import order_handler
except ImportError:  # pragma: no cover
    order_handler = None

app = FastAPI(title="HRMS", version="2.0.0")


def get_idempotency_key(idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> str:
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key header is required")
    try:
        parsed = uuid.UUID(idempotency_key)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key must be a valid UUID v4")
    if parsed.version != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key must be a valid UUID v4")
    return str(parsed)


@app.post("/api/v2/orders")
async def create_order(
    request: Request,
    response: Response,
    idempotency_key: str = Depends(get_idempotency_key),
) -> Any:
    payload: Dict[str, Any] = await request.json()

    if order_handler is None:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Order handler module is unavailable")

    result = await order_handler.handle_order_creation(payload=payload, idempotency_key=idempotency_key)

    if isinstance(result, dict) and result.get("is_replay") is True:
        response.headers["X-Idempotent-Replay"] = "true"
        return result["response"]

    return result


server_config = {
    "app": "app.main:app",
    "host": "0.0.0.0",
    "port": 8000,
    "reload": False,
}
