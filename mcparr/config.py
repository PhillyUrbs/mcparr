"""Configuration, persistence, and secret management for mcparr.

SQLite (via SQLModel) is the source of truth. Service API keys are encrypted at
rest with Fernet; the encryption key and the MCP bearer token live as
``0600`` files under the data directory (or are supplied via environment
variables for secret-manager setups).

Layout of the data directory (default ``/data``):

* ``mcparr.db``    - SQLite database (service instances + settings)
* ``secret.key``   - Fernet key (or ``MCPARR_SECRET_KEY`` env)
* ``mcp_token``    - MCP bearer token (or ``MCPARR_MCP_TOKEN`` env)
* ``audit.log``    - append-only audit trail
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .errors import ConfigError

# --------------------------------------------------------------------------- #
# Paths and environment
# --------------------------------------------------------------------------- #

ENV_DATA_DIR = "MCPARR_DATA_DIR"
ENV_SECRET_KEY = "MCPARR_SECRET_KEY"
ENV_MCP_TOKEN = "MCPARR_MCP_TOKEN"
ENV_ADMIN_PASSWORD = "MCPARR_ADMIN_PASSWORD"
ENV_SEED_FILE = "MCPARR_SEED_FILE"

DEFAULT_DATA_DIR = "/data"


def _now() -> datetime:
    return datetime.now(UTC)


def _write_secret_file(path: Path, content: str) -> None:
    """Write a secret to a file with ``0600`` permissions, umask-safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive perms from the start to avoid a race window.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    # Re-assert perms in case the file pre-existed with looser bits.
    with contextlib.suppress(OSError):
        # Best effort on platforms (e.g. Windows) that ignore POSIX perms.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


@dataclass
class AppPaths:
    """Resolved filesystem locations for runtime state."""

    data_dir: Path

    @classmethod
    def resolve(cls, data_dir: str | os.PathLike[str] | None = None) -> AppPaths:
        raw = data_dir or os.environ.get(ENV_DATA_DIR) or DEFAULT_DATA_DIR
        return cls(data_dir=Path(raw))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mcparr.db"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def mcp_token_path(self) -> Path:
        return self.data_dir / "mcp_token"

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.log"


# --------------------------------------------------------------------------- #
# Secret material: Fernet key + MCP token
# --------------------------------------------------------------------------- #


def load_or_create_fernet(paths: AppPaths) -> Fernet:
    """Return the Fernet cipher, creating a key on first boot if needed.

    Precedence: ``MCPARR_SECRET_KEY`` env > ``secret.key`` file > newly generated.
    A stable key is essential - without it, previously stored secrets become
    undecryptable across restarts.
    """
    env_key = os.environ.get(ENV_SECRET_KEY)
    if env_key:
        try:
            return Fernet(env_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"Invalid {ENV_SECRET_KEY}") from exc

    path = paths.secret_key_path
    if path.exists():
        return Fernet(path.read_bytes().strip())

    key = Fernet.generate_key()
    _write_secret_file(path, key.decode("utf-8"))
    return Fernet(key)


def load_or_create_mcp_token(paths: AppPaths) -> str:
    """Return the MCP bearer token, generating a strong one on first boot.

    Precedence: ``MCPARR_MCP_TOKEN`` env > ``mcp_token`` file > newly generated.
    Auth is always on, so a token is guaranteed to exist after this call.
    """
    env_token = os.environ.get(ENV_MCP_TOKEN)
    if env_token:
        token = env_token.strip()
        if not token:
            raise ConfigError(f"{ENV_MCP_TOKEN} is set but empty")
        return token

    path = paths.mcp_token_path
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    _write_secret_file(path, token)
    return token


def rotate_mcp_token(paths: AppPaths) -> str:
    """Generate and persist a new MCP token, returning it.

    Has no effect on an env-provided token (env takes precedence on next load),
    so callers should surface a warning when ``MCPARR_MCP_TOKEN`` is set.
    """
    token = secrets.token_urlsafe(32)
    _write_secret_file(paths.mcp_token_path, token)
    return token


def load_or_create_session_secret(paths: AppPaths) -> str:
    """Return the signing secret for UI session cookies, creating it if absent."""
    path = paths.data_dir / "session.key"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    secret = secrets.token_urlsafe(48)
    _write_secret_file(path, secret)
    return secret


class Secrets:
    """Encrypt/decrypt service secrets at rest using Fernet."""

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise ConfigError("Stored secret could not be decrypted") from exc


# --------------------------------------------------------------------------- #
# Persistence models
# --------------------------------------------------------------------------- #


class ServiceConfig(SQLModel, table=True):
    """A single configured service instance (multi-instance aware).

    ``slug`` is unique and is used to namespace the instance's MCP tools (for
    example ``radarr`` vs ``radarr4k``). The API key is stored encrypted.
    """

    __tablename__ = "service_config"

    instance_id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    service_type: str = Field(index=True)
    slug: str = Field(index=True, unique=True)
    label: str = ""
    base_url: str = ""
    api_key_encrypted: bytes = b""
    enabled: bool = True
    expose_destructive: bool = False
    default_quality_profile: str | None = None
    default_root_folder: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Setting(SQLModel, table=True):
    """Simple key/value store for app-wide settings."""

    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value: str = ""


@dataclass
class InstanceConfig:
    """Plaintext runtime view of a service instance handed to service modules.

    Distinct from :class:`ServiceConfig` (the encrypted-at-rest DB row): here the
    API key is decrypted for use by the service's HTTP client.
    """

    instance_id: str
    service_type: str
    slug: str
    label: str
    base_url: str
    api_key: str
    enabled: bool
    expose_destructive: bool
    default_quality_profile: str | None = None
    default_root_folder: str | None = None


# --------------------------------------------------------------------------- #
# Schema migrations (lightweight; see plan decision #4)
# --------------------------------------------------------------------------- #

SCHEMA_VERSION_KEY = "schema_version"

# Ordered migration callables. Index + 1 is the resulting schema version. The
# initial schema is produced by ``SQLModel.metadata.create_all`` so this list is
# empty until a real schema change lands; new migrations append here.
MIGRATIONS: list[Callable[[Engine], None]] = []


# --------------------------------------------------------------------------- #
# Database facade
# --------------------------------------------------------------------------- #


class Database:
    """Owns the SQLite engine and all config CRUD.

    DB operations are synchronous; SQLite access is local and fast, and writes
    are serialized by the caller (the ServiceManager lock) where it matters.
    """

    def __init__(self, engine: Engine, secrets_: Secrets) -> None:
        self._engine = engine
        self._secrets = secrets_
        self._password_hasher = PasswordHasher()

    @classmethod
    def open(cls, paths: AppPaths, secrets_: Secrets) -> Database:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{paths.db_path}",
            connect_args={"check_same_thread": False},
        )
        db = cls(engine, secrets_)
        db._init_schema()
        return db

    @property
    def engine(self) -> Engine:
        return self._engine

    def dispose(self) -> None:
        self._engine.dispose()

    # -- schema ----------------------------------------------------------- #

    def _init_schema(self) -> None:
        fresh = not self._table_exists("setting")
        SQLModel.metadata.create_all(self._engine)
        if fresh:
            # Brand-new DB already matches the latest models.
            self.set_setting(SCHEMA_VERSION_KEY, str(len(MIGRATIONS)))
            return
        self._run_migrations()

    def _table_exists(self, name: str) -> bool:
        from sqlalchemy import inspect

        return name in inspect(self._engine).get_table_names()

    def _run_migrations(self) -> None:
        current = int(self.get_setting(SCHEMA_VERSION_KEY) or "0")
        for index in range(current, len(MIGRATIONS)):
            MIGRATIONS[index](self._engine)
            self.set_setting(SCHEMA_VERSION_KEY, str(index + 1))

    # -- settings --------------------------------------------------------- #

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with Session(self._engine) as session:
            row = session.get(Setting, key)
            return row.value if row else default

    def set_setting(self, key: str, value: str) -> None:
        with Session(self._engine) as session:
            row = session.get(Setting, key)
            if row is None:
                row = Setting(key=key, value=value)
            else:
                row.value = value
            session.add(row)
            session.commit()

    # -- admin password (argon2id) --------------------------------------- #

    def is_admin_password_set(self) -> bool:
        return bool(self.get_setting("admin_password_hash"))

    def set_admin_password(self, password: str) -> None:
        if not password:
            raise ConfigError("Admin password must not be empty")
        self.set_setting("admin_password_hash", self._password_hasher.hash(password))

    def verify_admin_password(self, password: str) -> bool:
        stored = self.get_setting("admin_password_hash")
        if not stored:
            return False
        try:
            self._password_hasher.verify(stored, password)
        except VerifyMismatchError:
            return False
        if self._password_hasher.check_needs_rehash(stored):
            self.set_setting("admin_password_hash", self._password_hasher.hash(password))
        return True

    # -- language --------------------------------------------------------- #

    def get_language(self) -> str:
        return self.get_setting("language", "en") or "en"

    def set_language(self, lang: str) -> None:
        self.set_setting("language", lang)

    # -- service instances ------------------------------------------------ #

    def list_services(self) -> list[ServiceConfig]:
        with Session(self._engine) as session:
            return list(session.exec(select(ServiceConfig)).all())

    def get_service(self, instance_id: str) -> ServiceConfig | None:
        with Session(self._engine) as session:
            return session.get(ServiceConfig, instance_id)

    def get_service_by_slug(self, slug: str) -> ServiceConfig | None:
        with Session(self._engine) as session:
            return session.exec(
                select(ServiceConfig).where(ServiceConfig.slug == slug)
            ).first()

    def create_service(
        self,
        *,
        service_type: str,
        slug: str,
        label: str,
        base_url: str,
        api_key: str,
        enabled: bool = True,
        expose_destructive: bool = False,
        default_quality_profile: str | None = None,
        default_root_folder: str | None = None,
    ) -> ServiceConfig:
        if self.get_service_by_slug(slug):
            raise ConfigError(f"A service with slug '{slug}' already exists")
        row = ServiceConfig(
            service_type=service_type,
            slug=slug,
            label=label,
            base_url=base_url.rstrip("/"),
            api_key_encrypted=self._secrets.encrypt(api_key),
            enabled=enabled,
            expose_destructive=expose_destructive,
            default_quality_profile=default_quality_profile,
            default_root_folder=default_root_folder,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_service(
        self, instance_id: str, *, api_key: str | None = None, **fields: object
    ) -> ServiceConfig:
        with Session(self._engine) as session:
            row = session.get(ServiceConfig, instance_id)
            if row is None:
                raise ConfigError(f"Unknown service instance '{instance_id}'")
            for key, value in fields.items():
                if value is None:
                    continue
                if key == "base_url" and isinstance(value, str):
                    value = value.rstrip("/")
                setattr(row, key, value)
            if api_key:
                row.api_key_encrypted = self._secrets.encrypt(api_key)
            row.updated_at = _now()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def delete_service(self, instance_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(ServiceConfig, instance_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def to_instance_config(self, row: ServiceConfig) -> InstanceConfig:
        api_key = self._secrets.decrypt(row.api_key_encrypted) if row.api_key_encrypted else ""
        return InstanceConfig(
            instance_id=row.instance_id,
            service_type=row.service_type,
            slug=row.slug,
            label=row.label,
            base_url=row.base_url,
            api_key=api_key,
            enabled=row.enabled,
            expose_destructive=row.expose_destructive,
            default_quality_profile=row.default_quality_profile,
            default_root_folder=row.default_root_folder,
        )

    def load_enabled(self) -> list[InstanceConfig]:
        return [self.to_instance_config(r) for r in self.list_services() if r.enabled]


# --------------------------------------------------------------------------- #
# Optional seeding from environment / YAML (plan decision #6)
# --------------------------------------------------------------------------- #


@dataclass
class _SeedInstance:
    service_type: str
    slug: str
    api_key: str
    base_url: str = ""
    label: str = ""
    enabled: bool = True
    expose_destructive: bool = False
    default_quality_profile: str | None = None
    default_root_folder: str | None = None


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def seed_from_file(db: Database, seed_path: Path) -> SeedResult:
    """Seed service instances from a YAML file on boot (DB stays authoritative).

    Existing slugs are left untouched - seeding only fills in instances that do
    not yet exist, so live UI edits are never overwritten.
    """
    result = SeedResult()
    if not seed_path.exists():
        return result

    raw = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("services", [])
    if not isinstance(entries, list):
        raise ConfigError("Seed file 'services' must be a list")

    for entry in entries:
        try:
            seed = _SeedInstance(**entry)
        except TypeError as exc:
            raise ConfigError(f"Invalid seed entry: {entry!r}") from exc
        if db.get_service_by_slug(seed.slug):
            result.skipped.append(seed.slug)
            continue
        db.create_service(
            service_type=seed.service_type,
            slug=seed.slug,
            label=seed.label or seed.slug,
            base_url=seed.base_url,
            api_key=seed.api_key,
            enabled=seed.enabled,
            expose_destructive=seed.expose_destructive,
            default_quality_profile=seed.default_quality_profile,
            default_root_folder=seed.default_root_folder,
        )
        result.created.append(seed.slug)
    return result


def maybe_seed(db: Database, paths: AppPaths) -> SeedResult:
    """Seed from ``MCPARR_SEED_FILE`` or ``<data_dir>/seed.yaml`` if present."""
    env_path = os.environ.get(ENV_SEED_FILE)
    seed_path = Path(env_path) if env_path else paths.data_dir / "seed.yaml"
    return seed_from_file(db, seed_path)
