"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from mcparr.config import (
    AppPaths,
    Database,
    InstanceConfig,
    Secrets,
    load_or_create_fernet,
)
from mcparr.logging import AuditLog


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    return AppPaths(data_dir=tmp_path)


@pytest.fixture
def secrets_(paths: AppPaths) -> Secrets:
    return Secrets(load_or_create_fernet(paths))


@pytest.fixture
def db(paths: AppPaths, secrets_: Secrets):
    database = Database.open(paths, secrets_)
    yield database
    database.dispose()


@pytest.fixture
def audit(paths: AppPaths) -> AuditLog:
    return AuditLog(paths.audit_log_path)


def make_instance(
    *,
    slug: str = "radarr",
    service_type: str = "radarr",
    base_url: str = "http://radarr.test",
    expose_destructive: bool = False,
) -> InstanceConfig:
    return InstanceConfig(
        instance_id=f"id-{slug}",
        service_type=service_type,
        slug=slug,
        label=slug,
        base_url=base_url,
        api_key="test-key",
        enabled=True,
        expose_destructive=expose_destructive,
        default_quality_profile="HD-1080p",
        default_root_folder="/movies",
    )
