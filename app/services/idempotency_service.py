from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (Any, Awaitable, Callable, Dict, List, Optional, Protocol,
                    Tuple)

from fastapi import HTTPException, status

IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


@dataclass
class IdempotencyRecord:
    idempotency_key: str
    request_fingerprint: str
    request_payload_canonical: Any
    response_body: Dict[str, Any]
    response_status_code: int
    response_headers: Dict[str, str]
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return current >= self.expires_at


class IdempotencyStore(Protocol):
    async def get_record(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        ...

    async def save_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        ...

    async def delete_expired_records(self, now: Optional[datetime] = None) -> int:
        ...


@dataclass
class IdempotencyDecision:
    outcome: str
    response: Dict[str, Any]
    status_code: int
    headers: Dict[str, str]


def validate_idempotency_key(raw_key: Optional[str]) -> str:
    if raw_key is None or not raw_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    try:
        parsed = uuid.UUID(raw_key)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be a valid UUID v4",
        )
    if parsed.version != 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be a valid UUID v4",
        )
    return str(parsed)


def canonicalize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            str(key): canonicalize_payload(payload[key])
            for key in sorted(payload.keys(), key=lambda item: str(item))
        }
    if isinstance(payload, list):
        return [canonicalize_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [canonicalize_payload(item) for item in payload]
    if isinstance(payload, set):
        normalized_items = [canonicalize_payload(item) for item in payload]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str),
        )
    if isinstance(payload, datetime):
        if payload.tzinfo is None:
            payload = payload.replace(tzinfo=timezone.utc)
        return payload.astimezone(timezone.utc).isoformat()
    if isinstance(payload, uuid.UUID):
        return str(payload)
    return payload


def fingerprint_payload(payload: Any) -> str:
    canonical = canonicalize_payload(payload)
    serialized = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def diff_payloads(original: Any, current: Any, path: str = "") -> List[Dict[str, Any]]:
    original_canonical = canonicalize_payload(original)
    current_canonical = canonicalize_payload(current)
    diffs: List[Dict[str, Any]] = []

    def build_path(parent: str, key: str) -> str:
        if not parent:
            return key
        if key.startswith("["):
            return f"{parent}{key}"
        return f"{parent}.{key}"

    if isinstance(original_canonical, dict) and isinstance(current_canonical, dict):
        original_keys = set(original_canonical.keys())
        current_keys = set(current_canonical.keys())
        for key in sorted(original_keys - current_keys):
            diffs.append(
                {
                    "path": build_path(path, key),
                    "change_type": "removed",
                    "original_value": original_canonical[key],
                    "new_value": None,
                }
            )
        for key in sorted(current_keys - original_keys):
            diffs.append(
                {
                    "path": build_path(path, key),
                    "change_type": "added",
                    "original_value": None,
                    "new_value": current_canonical[key],
                }
            )
        for key in sorted(original_keys & current_keys):
            diffs.extend(diff_payloads(original_canonical[key], current_canonical[key], build_path(path, key)))
        return diffs

    if isinstance(original_canonical, list) and isinstance(current_canonical, list):
        max_len = max(len(original_canonical), len(current_canonical))
        for index in range(max_len):
            item_path = build_path(path, f"[{index}]")
            if index >= len(original_canonical):
                diffs.append(
                    {
                        "path": item_path,
                        "change_type": "added",
                        "original_value": None,
                        "new_value": current_canonical[index],
                    }
                )
            elif index >= len(current_canonical):
                diffs.append(
                    {
                        "path": item_path,
                        "change_type": "removed",
                        "original_value": original_canonical[index],
                        "new_value": None,
                    }
                )
            else:
                diffs.extend(diff_payloads(original_canonical[index], current_canonical[index], item_path))
        return diffs

    if original_canonical != current_canonical:
        diffs.append(
            {
                "path": path or "$",
                "change_type": "modified",
                "original_value": original_canonical,
                "new_value": current_canonical,
            }
        )
    return diffs


def serialize_response(
    response_body: Dict[str, Any],
    status_code: int,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    payload = {
        "response_body": copy.deepcopy(response_body),
        "status_code": status_code,
        "headers": dict(headers or {}),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def deserialize_response(serialized: str) -> Dict[str, Any]:
    parsed = json.loads(serialized)
    if not isinstance(parsed, dict):
        raise ValueError("Serialized response must decode to an object")
    response_body = parsed.get("response_body")
    status_code = parsed.get("status_code")
    headers = parsed.get("headers", {})
    if not isinstance(response_body, dict) or not isinstance(status_code, int) or not isinstance(headers, dict):
        raise ValueError("Serialized response is malformed")
    return {
        "response_body": response_body,
        "status_code": status_code,
        "headers": {str(key): str(value) for key, value in headers.items()},
    }


def build_order_response(payload: Dict[str, Any], idempotency_key: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    return {
        "order_id": str(uuid.uuid4()),
        "status": "created",
        "idempotency_key": idempotency_key,
        "created_at": current.isoformat(),
        "payload": copy.deepcopy(payload),
    }


@dataclass
class IdempotencyService:
    store: IdempotencyStore
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    async def handle(self, payload: Any, raw_idempotency_key: Optional[str]) -> Dict[str, Any]:
        idempotency_key = validate_idempotency_key(raw_idempotency_key)
        request_payload = self._validate_payload_object(payload)
        now = self._now()
        await self.store.delete_expired_records(now)

        record = await self.store.get_record(idempotency_key)
        current_fingerprint = fingerprint_payload(request_payload)
        canonical_payload = canonicalize_payload(request_payload)

        if record is not None and not record.is_expired(now):
            if record.request_fingerprint == current_fingerprint:
                replay_headers = self._normalize_replay_headers(record.response_headers)
                return {
                    "outcome": "replay",
                    "response": copy.deepcopy(record.response_body),
                    "status_code": 200,
                    "headers": replay_headers,
                }
            return {
                "outcome": "conflict",
                "response": {
                    "detail": "Idempotency key conflict",
                    "diff": diff_payloads(record.request_payload_canonical, canonical_payload),
                },
                "status_code": status.HTTP_409_CONFLICT,
                "headers": {},
            }

        response_body = build_order_response(request_payload, idempotency_key, now=now)
        response_headers = {"X-Idempotent-Replay": "true"}
        record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            request_fingerprint=current_fingerprint,
            request_payload_canonical=canonical_payload,
            response_body=copy.deepcopy(response_body),
            response_status_code=status.HTTP_201_CREATED,
            response_headers=response_headers,
            created_at=now,
            expires_at=now + self._ttl_delta(),
        )
        await self.store.save_record(record)
        return {
            "outcome": "inserted",
            "response": response_body,
            "status_code": status.HTTP_201_CREATED,
            "headers": {},
        }

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _ttl_delta(self):
        from datetime import timedelta

        return timedelta(seconds=IDEMPOTENCY_TTL_SECONDS)

    @staticmethod
    def _validate_payload_object(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be a JSON object")
        return payload

    @staticmethod
    def _normalize_replay_headers(headers: Dict[str, str]) -> Dict[str, str]:
        normalized = {str(key): str(value) for key, value in headers.items()}
        normalized["X-Idempotent-Replay"] = "true"
        return normalized

    @staticmethod
    def serialize_stored_record(record: IdempotencyRecord) -> str:
        return json.dumps(
            {
                "idempotency_key": record.idempotency_key,
                "request_fingerprint": record.request_fingerprint,
                "request_payload_canonical": record.request_payload_canonical,
                "response": serialize_response(
                    record.response_body,
                    record.response_status_code,
                    record.response_headers,
                ),
                "created_at": record.created_at.isoformat(),
                "expires_at": record.expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def deserialize_stored_record(serialized: str) -> IdempotencyRecord:
        parsed = json.loads(serialized)
        response = deserialize_response(parsed["response"])
        created_at = datetime.fromisoformat(parsed["created_at"])
        expires_at = datetime.fromisoformat(parsed["expires_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)
        return IdempotencyRecord(
            idempotency_key=parsed["idempotency_key"],
            request_fingerprint=parsed["request_fingerprint"],
            request_payload_canonical=parsed["request_payload_canonical"],
            response_body=response["response_body"],
            response_status_code=response["status_code"],
            response_headers=response["headers"],
            created_at=created_at,
            expires_at=expires_at,
        )
