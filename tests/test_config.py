"""Unit tests for config, encryption, CRUD, migrations, and seeding."""

from __future__ import annotations

import pytest

from mcparr.config import (
    SCHEMA_VERSION_KEY,
    Database,
    seed_from_file,
)
from mcparr.errors import ConfigError


def test_secret_round_trip(secrets_):
    ciphertext = secrets_.encrypt("super-secret-key")
    assert ciphertext != b"super-secret-key"
    assert secrets_.decrypt(ciphertext) == "super-secret-key"


def test_create_and_decrypt_service(db: Database):
    row = db.create_service(
        service_type="radarr",
        slug="radarr",
        label="Movies",
        base_url="http://radarr.test/",
        api_key="abc123",
    )
    assert row.base_url == "http://radarr.test"  # trailing slash stripped
    assert row.api_key_encrypted != b"abc123"
    instance = db.to_instance_config(row)
    assert instance.api_key == "abc123"


def test_duplicate_slug_rejected(db: Database):
    db.create_service(
        service_type="radarr", slug="radarr", label="", base_url="http://a", api_key="k"
    )
    with pytest.raises(ConfigError):
        db.create_service(
            service_type="radarr", slug="radarr", label="", base_url="http://b", api_key="k"
        )


def test_load_enabled_filters_disabled(db: Database):
    db.create_service(
        service_type="radarr", slug="on", label="", base_url="http://a", api_key="k",
        enabled=True,
    )
    db.create_service(
        service_type="sonarr", slug="off", label="", base_url="http://b", api_key="k",
        enabled=False,
    )
    enabled = db.load_enabled()
    assert [i.slug for i in enabled] == ["on"]


def test_update_service_rotates_api_key(db: Database):
    row = db.create_service(
        service_type="radarr", slug="radarr", label="", base_url="http://a", api_key="old"
    )
    db.update_service(row.instance_id, api_key="new", label="Renamed")
    updated = db.get_service(row.instance_id)
    assert updated is not None
    assert updated.label == "Renamed"
    assert db.to_instance_config(updated).api_key == "new"


def test_admin_password(db: Database):
    assert not db.is_admin_password_set()
    db.set_admin_password("hunter2pass")
    assert db.is_admin_password_set()
    assert db.verify_admin_password("hunter2pass")
    assert not db.verify_admin_password("wrong")


def test_schema_version_set_on_fresh_db(db: Database):
    assert db.get_setting(SCHEMA_VERSION_KEY) is not None


def test_seed_creates_and_skips(db: Database, tmp_path):
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        "services:\n"
        "  - service_type: radarr\n"
        "    slug: radarr\n"
        "    base_url: http://radarr.test\n"
        "    api_key: seedkey\n",
        encoding="utf-8",
    )
    first = seed_from_file(db, seed)
    assert first.created == ["radarr"]
    # Second run must not overwrite the existing instance.
    second = seed_from_file(db, seed)
    assert second.created == []
    assert second.skipped == ["radarr"]
