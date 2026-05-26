"""Tests voor `shared.crypto.rotate_global_secret` (BR-C01 + Config-flow)."""

from __future__ import annotations

from pathlib import Path

from shared.crypto import (
    derive_session_key,
    load_or_create_secret,
    make_pseudonym,
    rotate_global_secret,
)
from shared.models import EntityType


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_rotate_existing_key_archives_and_replaces(tmp_path: Path) -> None:
    secret_path = tmp_path / "secrets" / "global_secret.bin"
    secret_path.parent.mkdir()
    original = b"o" * 32
    secret_path.write_bytes(original)
    secret_path.chmod(0o600)

    archive_path = rotate_global_secret(secret_path)

    assert archive_path.parent == secret_path.parent
    assert archive_path.name.startswith(f"{secret_path.name}.archived-")
    assert archive_path.read_bytes() == original
    assert _mode(archive_path) == 0o600

    new_key = secret_path.read_bytes()
    assert len(new_key) == 32
    assert new_key != original
    assert _mode(secret_path) == 0o600


def test_rotate_without_existing_key_creates_one(tmp_path: Path) -> None:
    secret_path = tmp_path / "secrets" / "global_secret.bin"
    archive_path = rotate_global_secret(secret_path)

    assert archive_path.exists()
    assert archive_path.read_bytes() == b""
    assert _mode(archive_path) == 0o600

    assert secret_path.exists()
    assert len(secret_path.read_bytes()) == 32
    assert _mode(secret_path) == 0o600


def test_rotation_changes_session_pseudonyms(tmp_path: Path) -> None:
    """Na rotatie levert dezelfde input een ander pseudoniem op."""
    secret_path = tmp_path / "sec.bin"
    pre_secret = load_or_create_secret(secret_path)
    pre_session_key = derive_session_key(pre_secret, "sess-x")
    pre_pseudo = make_pseudonym(pre_session_key, "Pietersen", EntityType.NAME)

    rotate_global_secret(secret_path)
    post_secret = secret_path.read_bytes()
    post_session_key = derive_session_key(post_secret, "sess-x")
    post_pseudo = make_pseudonym(post_session_key, "Pietersen", EntityType.NAME)

    assert pre_pseudo != post_pseudo
