from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple

from fastapi import HTTPException, status

IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass
class IdempotencyRecord:
    idempotency_key: str
    request_fingerprint: str
    request_payload_canonical: Any
    response_body: Dict[str, Any]
    response_status_code: int
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
class InMemoryIdempotencyStore:
    records: Dict[str, IdempotencyRecord] = field(default_factory=dict)

    async def get_record(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        return self.records.get(idempotency_key)

    async def save_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self.records[record.idempotency_key] = record
        return record

    async def delete_expired_records(self, now: Optional[datetime] = None) -> int:
        current = now or datetime.now(timezone.utc)
        expired_keys = [key for key, record in self.records.items() if record.is_expired(current)]
        for key in expired_keys:
            del self.records[key]
        return len(expired_keys)


default_idempotency_store: InMemoryIdempotencyStore = InMemoryIdempotencyStore()


def canonicalize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(key): canonicalize_payload(payload[key]) for key in sorted(payload.keys(), key=lambda item: str(item))}
    if isinstance(payload, list):
        return [canonicalize_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [canonicalize_payload(item) for item in payload]
    if isinstance(payload, set):
        canonical_items = [canonicalize_payload(item) for item in payload]
        return sorted(canonical_items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if isinstance(payload, datetime):
        if payload.tzinfo is None:
            payload = payload.replace(tzinfo=timezone.utc)
        return payload.astimezone(timezone.utc).isoformat()
    if isinstance(payload, uuid.UUID):
        return str(payload)
    return payload


def fingerprint_payload(payload: Any) -> str:
    canonical = canonicalize_payload(payload)
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
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


def build_order_response(payload: Dict[str, Any], idempotency_key: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    order_id = str(uuid.uuid4())
    return {
        "order_id": order_id,
        "status": "created",
        "idempotency_key": idempotency_key,
        "created_at": current.isoformat(),
        "payload": copy.deepcopy(payload),
    }


def _validate_payload_object(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be a JSON object")
    return payload


async def handle_order_creation(
    payload: Any,
    idempotency_key: str,
    store: Optional[IdempotencyStore] = None,
) -> Dict[str, Any]:
    request_payload = _validate_payload_object(payload)
    idempotency_store = store or default_idempotency_store
    now = datetime.now(timezone.utc)
    await idempotency_store.delete_expired_records(now)

    record = await idempotency_store.get_record(idempotency_key)
    current_fingerprint = fingerprint_payload(request_payload)
    canonical_payload = canonicalize_payload(request_payload)

    if record is not None and not record.is_expired(now):
        if record.request_fingerprint == current_fingerprint:
            return {
                "is_replay": True,
                "response": record.response_body,
                "status_code": 200,
                "headers": {"X-Idempotent-Replay": "true"},
            }
        return {
            "is_replay": False,
            "response": {
                "detail": "Idempotency key conflict",
                "diff": diff_payloads(record.request_payload_canonical, canonical_payload),
            },
            "status_code": status.HTTP_409_CONFLICT,
        }

    response_body = build_order_response(request_payload, idempotency_key, now=now)
    new_record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        request_fingerprint=current_fingerprint,
        request_payload_canonical=canonical_payload,
        response_body=response_body,
        response_status_code=status.HTTP_201_CREATED,
        created_at=now,
        expires_at=now + IDEMPOTENCY_TTL,
    )
    await idempotency_store.save_record(new_record)
    return {
        "is_replay": False,
        "response": response_body,
        "status_code": status.HTTP_201_CREATED,
    }
