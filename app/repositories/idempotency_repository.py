from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional, Protocol, Sequence
from uuid import UUID


class CursorProtocol(Protocol):
    def execute(self, query: str, params: Sequence[Any] = ...) -> Any:
        ...

    def fetchone(self) -> Optional[Mapping[str, Any]]:
        ...

    def fetchall(self) -> List[Mapping[str, Any]]:
        ...

    @property
    def rowcount(self) -> int:
        ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...


@dataclass(frozen=True)
class IdempotencyRecord:
    idempotency_key: str
    request_hash: str
    canonical_request_body: Any
    response_status: int
    response_body: Any
    created_at: datetime
    expires_at: datetime


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_body(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _deserialize_body(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row_to_record(row: Mapping[str, Any]) -> IdempotencyRecord:
    return IdempotencyRecord(
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        canonical_request_body=_deserialize_body(row["canonical_request_body"]),
        response_status=int(row["response_status"]),
        response_body=_deserialize_body(row["response_body"]),
        created_at=_normalize_datetime(row["created_at"]),
        expires_at=_normalize_datetime(row["expires_at"]),
    )


class IdempotencyRepository:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def insert(self, record: IdempotencyRecord) -> IdempotencyRecord:
        query = """
            INSERT INTO idempotency_records (
                idempotency_key,
                request_hash,
                canonical_request_body,
                response_status,
                response_body,
                created_at,
                expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING idempotency_key, request_hash, canonical_request_body, response_status, response_body, created_at, expires_at
        """
        params = (
            record.idempotency_key,
            record.request_hash,
            _serialize_body(record.canonical_request_body),
            record.response_status,
            _serialize_body(record.response_body),
            _normalize_datetime(record.created_at),
            _normalize_datetime(record.expires_at),
        )
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
            self._connection.commit()
            if row is None:
                return record
            return _row_to_record(row)
        except Exception:
            self._connection.rollback()
            raise

    def get_by_key(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        query = """
            SELECT idempotency_key, request_hash, canonical_request_body, response_status, response_body, created_at, expires_at
            FROM idempotency_records
            WHERE idempotency_key = %s
            LIMIT 1
        """
        with self._connection.cursor() as cursor:
            cursor.execute(query, (idempotency_key,))
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def update_response(self, idempotency_key: str, response_status: int, response_body: Any) -> Optional[IdempotencyRecord]:
        query = """
            UPDATE idempotency_records
            SET response_status = %s,
                response_body = %s
            WHERE idempotency_key = %s
            RETURNING idempotency_key, request_hash, canonical_request_body, response_status, response_body, created_at, expires_at
        """
        params = (
            response_status,
            _serialize_body(response_body),
            idempotency_key,
        )
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
            self._connection.commit()
            if row is None:
                return None
            return _row_to_record(row)
        except Exception:
            self._connection.rollback()
            raise

    def update_expiration(self, idempotency_key: str, expires_at: datetime) -> Optional[IdempotencyRecord]:
        query = """
            UPDATE idempotency_records
            SET expires_at = %s
            WHERE idempotency_key = %s
            RETURNING idempotency_key, request_hash, canonical_request_body, response_status, response_body, created_at, expires_at
        """
        params = (_normalize_datetime(expires_at), idempotency_key)
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
            self._connection.commit()
            if row is None:
                return None
            return _row_to_record(row)
        except Exception:
            self._connection.rollback()
            raise

    def delete_older_than(self, cutoff: Optional[datetime] = None) -> int:
        effective_cutoff = _normalize_datetime(cutoff or (datetime.now(timezone.utc) - timedelta(hours=24)))
        query = """
            DELETE FROM idempotency_records
            WHERE created_at < %s
        """
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(query, (effective_cutoff,))
                deleted = cursor.rowcount
            self._connection.commit()
            return deleted
        except Exception:
            self._connection.rollback()
            raise

    def list_expired(self, before: Optional[datetime] = None) -> List[IdempotencyRecord]:
        effective_before = _normalize_datetime(before or datetime.now(timezone.utc))
        query = """
            SELECT idempotency_key, request_hash, canonical_request_body, response_status, response_body, created_at, expires_at
            FROM idempotency_records
            WHERE expires_at < %s
            ORDER BY expires_at ASC
        """
        with self._connection.cursor() as cursor:
            cursor.execute(query, (effective_before,))
            rows = cursor.fetchall()
        return [_row_to_record(row) for row in rows]
