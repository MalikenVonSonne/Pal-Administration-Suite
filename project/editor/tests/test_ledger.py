from datetime import datetime
from pathlib import Path

import pytest

from pal_editor.ledger import (
    BackupApprovalRequired,
    BackupPolicy,
    LedgerError,
    OperationLedger,
    timestamped_backup_name,
)


def test_ledger_records_only_actual_draft_changes(tmp_path: Path) -> None:
    source = tmp_path / "Level.sav"
    target = tmp_path / "Level-edited.sav"
    ledger = OperationLedger(source, target)

    changes = ledger.record_draft(
        {"nickname": "Moss", "level": 10, "unknown": {"keep": True}},
        {"nickname": "Fern", "level": 10, "unknown": {"keep": True}},
    )

    assert ledger.dirty is True
    assert ledger.changed_fields == ("nickname",)
    assert ledger.before_fields == {"nickname": "Moss"}
    assert ledger.after_fields == {"nickname": "Fern"}
    assert changes[0].before == "Moss"
    assert changes[0].after == "Fern"


def test_reverting_a_field_clears_dirty_state() -> None:
    ledger = OperationLedger("source.sav", "target.sav")

    ledger.set_field("level", 10, 11)
    assert ledger.dirty is True

    ledger.set_field("level", 10, 10)

    assert ledger.changed_fields == ()
    assert ledger.dirty is False


def test_validation_messages_accept_issue_like_objects_without_dirtying_draft() -> None:
    class Issue:
        message = "Level is outside the supported range"

    ledger = OperationLedger("source.sav", "target.sav")
    ledger.set_validation_messages([Issue(), "Nickname is too long", "Nickname is too long"])

    assert ledger.validation_messages == [
        "Level is outside the supported range",
        "Nickname is too long",
    ]
    assert ledger.dirty is False


def test_source_and_target_must_be_distinct() -> None:
    with pytest.raises(LedgerError, match="[Ss]ource.*target"):
        OperationLedger("save.sav", Path(".") / "save.sav")


def test_backup_policy_is_conservative_and_timestamped(tmp_path: Path) -> None:
    source = tmp_path / "Level.sav"
    backup_dir = tmp_path / "backups"
    source.write_bytes(b"original save")
    stamp = datetime(2026, 7, 14, 22, 30, 5, 123456)
    ledger = OperationLedger(source, tmp_path / "edited.sav", backup_policy="always")

    backup = ledger.create_backup(backup_dir=backup_dir, timestamp=stamp)

    assert backup == backup_dir / "Level.backup-20260714-223005-123456.sav.bak"
    assert backup.read_bytes() == b"original save"
    assert source.read_bytes() == b"original save"
    assert ledger.backup_path == backup


def test_existing_backup_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "Level.sav"
    backup_dir = tmp_path / "backups"
    source.write_bytes(b"new source")
    backup_dir.mkdir()
    existing = backup_dir / "Level.backup-20260714-223005-123456.sav.bak"
    existing.write_bytes(b"older backup")
    ledger = OperationLedger(source, tmp_path / "edited.sav")

    backup = ledger.create_backup(backup_dir=backup_dir, timestamp=datetime(2026, 7, 14, 22, 30, 5, 123456))

    assert backup != existing
    assert existing.read_bytes() == b"older backup"
    assert backup is not None and backup.read_bytes() == b"new source"


def test_ask_requires_approval_and_off_does_not_copy(tmp_path: Path) -> None:
    source = tmp_path / "Level.sav"
    source.write_bytes(b"save")

    ask = OperationLedger(source, tmp_path / "ask.sav", backup_policy=BackupPolicy.ASK)
    with pytest.raises(BackupApprovalRequired):
        ask.create_backup()
    assert ask.create_backup(approved=True) is not None

    off = OperationLedger(source, tmp_path / "off.sav", backup_policy=BackupPolicy.OFF)
    assert off.create_backup() is None
    assert list(tmp_path.glob("off*")) == []


def test_mark_clean_preserves_audit_diff() -> None:
    ledger = OperationLedger("source.sav", "target.sav")
    ledger.set_field("iv_hp", 20, 100)

    ledger.mark_clean()

    assert ledger.dirty is False
    assert ledger.changed_fields == ("iv_hp",)
    assert ledger.changes[0].before == 20


def test_timestamped_backup_name_keeps_source_suffix() -> None:
    name = timestamped_backup_name(
        "Level.sav",
        datetime(2026, 7, 14, 1, 2, 3, 4),
    )

    assert name == "Level.backup-20260714-010203-000004.sav.bak"
