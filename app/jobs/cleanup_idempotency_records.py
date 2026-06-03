from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol, runtime_checkable

from app.repositories import idempotency_repository

logger = logging.getLogger(__name__)

IDEMPOTENCY_TTL = timedelta(hours=24)


@runtime_checkable
class ConnectionFactory(Protocol):
    def __call__(self) -> object:
        ...


def _normalize_cutoff(cutoff: Optional[datetime]) -> datetime:
    if cutoff is None:
        return datetime.now(timezone.utc) - IDEMPOTENCY_TTL
    if cutoff.tzinfo is None:
        return cutoff.replace(tzinfo=timezone.utc)
    return cutoff.astimezone(timezone.utc)


def _close_connection(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def _delete_expired_rows(connection: object, cutoff: datetime) -> int:
    repository = idempotency_repository.IdempotencyRepository(connection)  # type: ignore[arg-type]
    if hasattr(repository, "delete_older_than"):
        return repository.delete_older_than(cutoff)
    if hasattr(repository, "list_expired"):
        expired_records = repository.list_expired(before=cutoff)
        deleted = 0
        if expired_records:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "DELETE FROM idempotency_records WHERE expires_at < %s",
                    (cutoff,),
                )
                deleted = cursor.rowcount
            connection.commit()  # type: ignore[attr-defined]
        return deleted
    raise AttributeError("IdempotencyRepository does not provide an expiration cleanup method")


async def cleanup_expired_idempotency_records(
    connection_factory: Callable[[], object],
    cutoff: Optional[datetime] = None,
) -> int:
    resolved_cutoff = _normalize_cutoff(cutoff)
    logger.info("Starting idempotency cleanup job for cutoff=%s", resolved_cutoff.isoformat())
    connection = connection_factory()
    try:
        deleted = await asyncio.to_thread(_delete_expired_rows, connection, resolved_cutoff)
        logger.info(
            "Completed idempotency cleanup job for cutoff=%s deleted=%s",
            resolved_cutoff.isoformat(),
            deleted,
        )
        return deleted
    except Exception:
        logger.exception("Idempotency cleanup job failed for cutoff=%s", resolved_cutoff.isoformat())
        raise
    finally:
        _close_connection(connection)


async def run_cleanup_job(
    connection_factory: Callable[[], object],
    cutoff: Optional[datetime] = None,
) -> int:
    result = cleanup_expired_idempotency_records(connection_factory=connection_factory, cutoff=cutoff)
    if inspect.isawaitable(result):
        return await result
    return result


def main() -> int:
    raise RuntimeError(
        "cleanup job requires a connection_factory to be injected by the caller"
    )


if __name__ == "__main__":
    raise SystemExit(main())
