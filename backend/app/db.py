"""Neon/PostgreSQL configuration and minimal connection handling. Standard library only.

The database driver itself (``psycopg`` v3 preferred, ``psycopg2``
accepted — both DBAPI 2.0 with ``%s`` placeholders) is imported lazily
inside :func:`connect`, so file-backed mode, the full test suite, and
``pip install -e .`` never require it. Only database-backed mode
needs a driver installed. No connection string, username, password,
or token is ever hardcoded, logged, or echoed: every error path
passes through :func:`redact_dsn`.

Serverless note: pools are caller-owned, small, and closed
explicitly. There are no global connections and no background
reconnects — a serverless instance creates its pool on demand and
closes it on shutdown.
"""

from __future__ import annotations

import os
import queue
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlsplit, urlunsplit

__all__ = [
    "DbConfig",
    "DbConfigError",
    "DbConnectionError",
    "ConnectionPool",
    "apply_migrations",
    "connect",
    "db_config_from_env",
    "redact_dsn",
]

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

_SSLMODES_STRICT = ("require", "verify-ca", "verify-full")


class DbConfigError(ValueError):
    """Invalid or missing database configuration."""


class DbConnectionError(RuntimeError):
    """Database connection or migration failure. Messages are already
    redacted: they never contain connection strings or secrets."""


@dataclass(frozen=True)
class DbConfig:
    """Validated database configuration. ``dsn`` is the pooled Neon URL
    for application traffic; ``unpooled_dsn`` (same value unless a
    separate migration URL is configured) is for schema migrations."""

    dsn: str
    unpooled_dsn: str
    pool_max: int = 5
    connect_timeout_seconds: int = 10
    statement_timeout_ms: int = 30_000
    require_ssl: bool = True


def _pool_max(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return 5
    try:
        value = int(raw.strip())
    except ValueError:
        raise DbConfigError(f"TAXGUARD_DB_POOL_MAX must be an integer, got {raw!r}") from None
    if not 1 <= value <= 20:
        raise DbConfigError(f"TAXGUARD_DB_POOL_MAX must be 1..20, got {value}")
    return value


def _timeout(raw: str | None, name: str, default: int, low: int, high: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise DbConfigError(f"{name} must be an integer, got {raw!r}") from None
    if not low <= value <= high:
        raise DbConfigError(f"{name} must be {low}..{high}, got {value}")
    return value


def redact_dsn(dsn: str) -> str:
    """Return the DSN with any password replaced by ``***``. Safe to log."""
    try:
        parts = urlsplit(dsn)
        if parts.password is None:
            return dsn
        netloc = parts.hostname or ""
        if parts.username:
            netloc = f"{parts.username}:***@{netloc}"
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        return "<unparseable-dsn>"


def _has_strict_ssl(dsn: str) -> bool:
    try:
        query = dict(parse_qsl(urlsplit(dsn).query, keep_blank_values=True))
    except ValueError:
        return False
    return query.get("sslmode", "").lower() in _SSLMODES_STRICT


def _is_neon_host(dsn: str) -> bool:
    try:
        host = (urlsplit(dsn).hostname or "").lower()
    except ValueError:
        return False
    return host == "neon.tech" or host.endswith(".neon.tech")


def _require_nonempty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DbConfigError(f"{name} must be a non-empty string")
    return value.strip()


def db_config_from_env(*, required: bool = False) -> DbConfig | None:
    """Build :class:`DbConfig` from the environment.

    Reads ``TAXGUARD_DATABASE_URL`` (pooled application URL),
    ``TAXGUARD_DATABASE_URL_UNPOOLED`` (migrations; falls back to the
    pooled URL), ``TAXGUARD_DB_POOL_MAX`` (1..20, default 5),
    ``TAXGUARD_DB_CONNECT_TIMEOUT`` (1..60s, default 10),
    ``TAXGUARD_DB_STATEMENT_TIMEOUT_MS`` (up to 300000, default 30000),
    and ``TAXGUARD_DB_REQUIRE_SSL`` (default true).

    Returns ``None`` when no URL is set and ``required`` is False
    (file-backed mode). Raises :class:`DbConfigError` when required
    settings are missing or invalid — including a Neon host without a
    strict ``sslmode``.
    """
    raw = os.getenv("TAXGUARD_DATABASE_URL", "")
    if not raw.strip():
        if required:
            raise DbConfigError("TAXGUARD_DATABASE_URL is required for database-backed mode")
        return None
    dsn = _require_nonempty("TAXGUARD_DATABASE_URL", raw)
    if urlsplit(dsn).scheme not in ("postgres", "postgresql"):
        raise DbConfigError("TAXGUARD_DATABASE_URL must be a postgres(dsn) URL")
    unpooled = os.getenv("TAXGUARD_DATABASE_URL_UNPOOLED", "").strip() or dsn
    _require_nonempty("TAXGUARD_DATABASE_URL_UNPOOLED", unpooled)
    require_ssl = os.getenv("TAXGUARD_DB_REQUIRE_SSL", "true").strip().lower() not in (
        "0", "false", "no",
    )
    if require_ssl and _is_neon_host(dsn) and not _has_strict_ssl(dsn):
        raise DbConfigError(
            "Neon hosts require a strict sslmode (require, verify-ca, or verify-full) "
            "in TAXGUARD_DATABASE_URL"
        )
    return DbConfig(
        dsn=dsn,
        unpooled_dsn=unpooled,
        pool_max=_pool_max(os.getenv("TAXGUARD_DB_POOL_MAX")),
        connect_timeout_seconds=_timeout(
            os.getenv("TAXGUARD_DB_CONNECT_TIMEOUT"),
            "TAXGUARD_DB_CONNECT_TIMEOUT", 10, 1, 60,
        ),
        statement_timeout_ms=_timeout(
            os.getenv("TAXGUARD_DB_STATEMENT_TIMEOUT_MS"),
            "TAXGUARD_DB_STATEMENT_TIMEOUT_MS", 30_000, 1, 300_000,
        ),
        require_ssl=require_ssl,
    )


def _driver() -> Any:
    """Lazily import a DBAPI driver: psycopg v3 preferred, psycopg2 fallback."""
    try:
        import psycopg  # type: ignore[import-not-found]

        return psycopg
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore[import-not-found]

        return psycopg2
    except ImportError:
        raise DbConnectionError(
            "database-backed mode needs a PostgreSQL driver: "
            "install psycopg[binary] (preferred) or psycopg2"
        ) from None


def connect(config: DbConfig, *, unpooled: bool = False) -> Any:
    """Open one DBAPI connection with timeouts applied. The caller owns
    it and must close it (or hand it to :class:`ConnectionPool`).
    Failures raise :class:`DbConnectionError` with the DSN redacted."""
    dsn = config.unpooled_dsn if unpooled else config.dsn
    try:
        conn = _driver().connect(dsn, connect_timeout=config.connect_timeout_seconds)
    except DbConnectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - type only; driver text is never trusted
        raise DbConnectionError(
            f"could not connect to database at {redact_dsn(dsn)} "
            f"({type(exc).__name__})"
        ) from None
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (str(config.statement_timeout_ms),))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - type only; driver text is never trusted
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
        raise DbConnectionError(
            f"could not configure database session at {redact_dsn(dsn)} "
            f"({type(exc).__name__})"
        ) from None
    return conn


class ConnectionPool:
    """Minimal bounded pool over short-lived connections. Caller-owned:
    create per process, close explicitly (no globals). Connections are
    created lazily up to ``maxsize`` and validated only by use — a
    broken connection surfaces as a redacted :class:`DbConnectionError`
    from the failing operation, never as a leaked secret."""

    def __init__(self, config: DbConfig, *, maxsize: int | None = None) -> None:
        self._config = config
        self._maxsize = maxsize or config.pool_max
        self._idle: queue.Queue = queue.Queue(maxsize=self._maxsize)
        self._open = 0
        self._closed = False

    @contextmanager
    def acquire(self) -> Iterator[Any]:
        """Yield one connection, returning it afterwards. Raises
        :class:`DbConnectionError` when the pool is closed or a new
        connection cannot be established."""
        if self._closed:
            raise DbConnectionError("connection pool is closed")
        try:
            conn = self._idle.get_nowait()
        except queue.Empty:
            if self._open >= self._maxsize:
                raise DbConnectionError(
                    f"connection pool exhausted ({self._maxsize} connections)"
                ) from None
            conn = connect(self._config)
            self._open += 1
        try:
            yield conn
        except Exception:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup
                pass
            self._open -= 1
            raise
        if self._closed:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup
                pass
            self._open -= 1
        else:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001, S110 - best-effort reset
                pass
            self._idle.put_nowait(conn)

    def close(self) -> None:
        """Close every idle connection. Idempotent."""
        self._closed = True
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup
                pass
            self._open -= 1


def apply_migrations(conn: Any, migrations_dir: str = MIGRATIONS_DIR) -> tuple[str, ...]:
    """Apply pending ``*.sql`` migrations in filename order, each in
    its own transaction, recording versions in ``schema_migrations``.
    Re-runs are no-ops for applied versions. Returns applied versions.
    Failures roll back the in-flight migration and raise
    :class:`DbConnectionError` (redacted). Down-migrations
    (``*.down.sql``) are documentation, never applied here."""
    import glob

    files = sorted(
        path for path in glob.glob(os.path.join(migrations_dir, "*.sql"))
        if not path.endswith(".down.sql")
    )
    if not files:
        raise DbConnectionError(f"no migrations found in {migrations_dir}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()
        done: list[str] = []
        for path in files:
            version = os.path.splitext(os.path.basename(path))[0]
            if version in applied:
                continue
            with open(path, encoding="utf-8") as handle:
                sql = handle.read()
            if not sql.strip():
                raise DbConnectionError(f"migration {version} is empty")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )
            conn.commit()
            done.append(version)
        return tuple(done)
    except DbConnectionError:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
        raise
    except Exception as exc:  # noqa: BLE001 - redacted below
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass
        raise DbConnectionError(
            f"migration failed: {type(exc).__name__}: {exc}"
        ) from None
