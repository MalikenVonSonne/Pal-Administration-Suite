from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pal_editor.backup_store import (
    BACKUP_SCHEMA_VERSION,
    GROUP_MANIFEST_FILENAME,
    RETENTION_LIMIT,
    BackupStage,
    BackupStore,
    BackupStoreError,
    default_backup_root,
)
from pal_editor.safe_save import (
    FailureStage,
    RecoveryResult,
    SafeSaveTransaction,
    SourceConfidence,
    TransactionRequest,
    fingerprint_file,
)


class BackupTestClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


class CountingRenamer:
    def __init__(self, fail_on: int | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def __call__(self, source: Path, destination: Path) -> None:
        self.calls += 1
        if self.calls == self.fail_on:
            raise OSError("injected finalization failure")
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(source, destination)


def make_source(tmp_path: Path, name: str = "Level.sav", payload: bytes = b"original") -> Path:
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return source


def make_store(tmp_path: Path, **kwargs) -> BackupStore:
    return BackupStore(tmp_path / "PalAdminBackups", **kwargs)


def create_records(store: BackupStore, source: Path, count: int) -> list:
    records = []
    for _ in range(count):
        records.append(store.create_backup(fingerprint_file(source)).record)
        if hasattr(store.clock, "advance"):
            store.clock.advance()
    return records


def test_default_root_is_local_app_data_and_does_not_write(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    root = default_backup_root()
    assert root == (tmp_path / "LocalAppData" / "PalAdmin" / "Backups").resolve()
    assert not root.exists()


def test_source_identity_groups_paths_not_filenames_and_case_aliases_match(tmp_path: Path):
    first = make_source(tmp_path / "world-a", name="Level.sav")
    second = make_source(tmp_path / "world-b", name="Level.sav")
    store = make_store(tmp_path)
    assert store.source_identity(first).group_id != store.source_identity(second).group_id
    assert store.source_identity(str(first).upper()).group_id == store.source_identity(first).group_id
    assert store.group_directory(first).parent == store.root
    assert "Level.sav" not in store.group_directory(first).name


def test_unsafe_source_filename_cannot_escape_group_or_root(tmp_path: Path):
    source = make_source(tmp_path, name="[unsafe]..name.sav")
    store = make_store(tmp_path)
    group = store.group_directory(source)
    assert group.parent == store.root
    assert group.resolve().is_relative_to(store.root.resolve())


def test_backup_creation_copies_exact_bytes_and_writes_verified_metadata(tmp_path: Path):
    source = make_source(tmp_path)
    clock = BackupTestClock()
    store = make_store(tmp_path, clock=clock, id_factory=lambda: "txn-1")
    result = store.create_backup(fingerprint_file(source), transaction_id="transaction-1")
    assert result.proof.verified and result.proof.durable
    assert result.record.backup_path.read_bytes() == source.read_bytes()
    metadata = json.loads(result.record.metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == BACKUP_SCHEMA_VERSION
    assert metadata["canonical_source_path"] == str(source.resolve())
    assert metadata["source_group_id"] == store.source_identity(source).group_id
    assert metadata["transaction_id"] == "transaction-1"
    assert metadata["backup_filename"] == result.record.backup_path.name
    assert metadata["verification_status"] == "verified"
    reopened = store.verify_record(result.record.metadata_path)
    assert reopened.backup_path == result.record.backup_path
    assert not list(store.group_directory(source).glob(".paladmin-*.tmp"))


def test_proof_is_accepted_by_safe_save_transaction_and_backup_survives(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path)
    result = SafeSaveTransaction().run(
        TransactionRequest(
            source_path=source,
            baseline=fingerprint_file(source),
            backup_provider=store,
            serialize_output=lambda path: path.write_bytes(b"edited"),
            validate_output=lambda path: True,
        )
    )
    assert result.success
    records, warnings = store.list_verified_backups(source)
    assert len(records) == 1
    assert not warnings
    assert records[0].backup_path.read_bytes() == b"original"


def test_source_missing_and_baseline_mismatch_are_rejected(tmp_path: Path):
    source = make_source(tmp_path)
    baseline = fingerprint_file(source)
    source.unlink()
    with pytest.raises(BackupStoreError) as missing:
        make_store(tmp_path).create_backup(baseline)
    assert missing.value.stage is BackupStage.SOURCE_VERIFICATION

    source = make_source(tmp_path, payload=b"changed")
    with pytest.raises(BackupStoreError) as mismatch:
        make_store(tmp_path).create_backup(baseline)
    assert mismatch.value.stage is BackupStage.SOURCE_VERIFICATION


def test_source_change_during_backup_is_detected_and_no_verified_record_exists(tmp_path: Path):
    source = make_source(tmp_path)

    def copy_then_change(src: Path, dest: Path) -> None:
        shutil.copyfile(src, dest)
        src.write_bytes(b"changed during backup")

    store = make_store(tmp_path, copy_file=copy_then_change)
    with pytest.raises(BackupStoreError) as raised:
        store.create_backup(fingerprint_file(source))
    assert raised.value.stage is BackupStage.SOURCE_VERIFICATION
    assert store.list_verified_backups(source)[0] == ()
    assert source.read_bytes() == b"changed during backup"


@pytest.mark.parametrize(
    "store_kwargs, expected_stage",
    [
        ({"directory_creator": lambda path: (_ for _ in ()).throw(OSError("root failure"))}, BackupStage.ROOT),
        ({"temporary_creator": lambda directory, prefix, suffix: (_ for _ in ()).throw(OSError("temp failure"))}, BackupStage.TEMPORARY_WRITE),
        ({"copy_file": lambda source, dest: (_ for _ in ()).throw(OSError("copy failure"))}, BackupStage.COPY),
        ({"durabilizer": lambda path: (_ for _ in ()).throw(OSError("fsync failure"))}, BackupStage.DURABILITY),
        ({"copy_file": lambda source, dest: dest.write_bytes(b"wrong bytes")}, BackupStage.FINGERPRINT),
    ],
)
def test_backup_creation_failures_return_structured_errors_and_no_proof(
    tmp_path: Path,
    store_kwargs,
    expected_stage: BackupStage,
):
    source = make_source(tmp_path)
    store = make_store(tmp_path, **store_kwargs)
    with pytest.raises(BackupStoreError) as raised:
        store.create_backup(fingerprint_file(source))
    assert raised.value.stage is expected_stage
    assert source.read_bytes() == b"original"
    records, _warnings = store.list_verified_backups(source)
    assert records == ()


def test_backup_and_metadata_finalization_failures_do_not_issue_proof(tmp_path: Path):
    source = make_source(tmp_path)
    renamer = CountingRenamer(fail_on=2)
    with pytest.raises(BackupStoreError) as backup_rename:
        make_store(tmp_path, renamer=renamer).create_backup(fingerprint_file(source))
    assert backup_rename.value.stage is BackupStage.FINALIZATION

    renamer = CountingRenamer(fail_on=3)
    with pytest.raises(BackupStoreError) as metadata_rename:
        make_store(tmp_path / "second", renamer=renamer).create_backup(fingerprint_file(source))
    assert metadata_rename.value.stage is BackupStage.FINALIZATION


def test_metadata_write_durability_and_reparse_failures_are_safe(tmp_path: Path):
    source = make_source(tmp_path)
    calls = 0

    def metadata_writer(path: Path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("metadata write failure")
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    with pytest.raises(BackupStoreError) as write_failure:
        make_store(tmp_path, metadata_writer=metadata_writer).create_backup(fingerprint_file(source))
    assert write_failure.value.stage is BackupStage.METADATA

    calls = 0

    def durabilizer(path):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("metadata durability failure")

    with pytest.raises(BackupStoreError) as durable_failure:
        make_store(tmp_path / "durable", durabilizer=durabilizer).create_backup(fingerprint_file(source))
    assert durable_failure.value.stage is BackupStage.DURABILITY

    def broken_reader(path):
        raise ValueError("metadata reparse failure")

    with pytest.raises(BackupStoreError) as reparse_failure:
        make_store(tmp_path / "reparse", metadata_reader=broken_reader).create_backup(fingerprint_file(source))
    assert reparse_failure.value.stage is BackupStage.VERIFICATION


def test_unique_names_handle_duplicate_timestamp_and_id(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path, clock=BackupTestClock(), id_factory=lambda: "same-id")
    first = store.create_backup(fingerprint_file(source)).record
    second = store.create_backup(fingerprint_file(source)).record
    assert first.backup_path != second.backup_path
    assert first.metadata_path != second.metadata_path
    assert first.backup_path.exists() and second.backup_path.exists()


def test_verification_rejects_future_schema_altered_metadata_missing_and_corrupt_backup(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path)
    record = store.create_backup(fingerprint_file(source)).record

    payload = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    payload["schema_version"] = BACKUP_SCHEMA_VERSION + 1
    record.metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupStoreError):
        store.verify_record(record.metadata_path)

    record = store.create_backup(fingerprint_file(source)).record
    payload = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    payload["backup_fingerprint"]["sha256"] = "0" * 64
    record.metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupStoreError):
        store.verify_record(record.metadata_path)

    record = store.create_backup(fingerprint_file(source)).record
    record.backup_path.unlink()
    with pytest.raises(BackupStoreError):
        store.verify_record(record.metadata_path)

    record = store.create_backup(fingerprint_file(source)).record
    record.backup_path.write_bytes(b"corrupted")
    with pytest.raises(BackupStoreError):
        store.verify_record(record.metadata_path)


def test_wrong_group_and_malformed_files_are_preserved(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path)
    record = store.create_backup(fingerprint_file(source)).record
    other_group = store.root / "source-other"
    other_group.mkdir(parents=True)
    copied_metadata = other_group / record.metadata_path.name
    shutil.copyfile(record.metadata_path, copied_metadata)
    with pytest.raises(BackupStoreError):
        store.verify_record(copied_metadata)

    orphan = store.group_directory(source) / "orphan.sav"
    orphan.write_bytes(b"orphan")
    malformed = store.group_directory(source) / "malformed.json"
    malformed.write_text("not json", encoding="utf-8")
    notes = store.group_directory(source) / "notes.txt"
    notes.write_text("keep", encoding="utf-8")
    result = store.prune_verified_backups(source)
    assert result.warnings
    assert orphan.exists() and malformed.exists() and notes.exists()


def test_retention_keeps_latest_five_and_prunes_oldest_first(tmp_path: Path):
    source = make_source(tmp_path)
    counter = {"value": 0}

    def id_factory() -> str:
        counter["value"] += 1
        return f"id-{counter['value']:04d}"

    store = make_store(tmp_path, clock=BackupTestClock(), id_factory=id_factory)
    records = create_records(store, source, RETENTION_LIMIT + 2)
    result = store.prune_verified_backups(source)
    assert result.success
    assert len(result.retained) == RETENTION_LIMIT
    assert len(result.removed) == 2
    assert all(record.backup_path.exists() for record in result.retained)
    assert not records[0].backup_path.exists()
    assert not records[1].backup_path.exists()

def test_retention_isolated_between_same_named_sources(tmp_path: Path):
    first = make_source(tmp_path / "world-a")
    second = make_source(tmp_path / "world-b")
    store = make_store(tmp_path, clock=BackupTestClock())
    create_records(store, first, RETENTION_LIMIT + 2)
    create_records(store, second, 1)
    result = store.prune_verified_backups(first)
    assert len(result.retained) == RETENTION_LIMIT
    assert len(store.list_verified_backups(second)[0]) == 1


def test_retention_with_fewer_or_exactly_five_records_removes_nothing(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path, clock=BackupTestClock())
    create_records(store, source, RETENTION_LIMIT - 1)
    fewer = store.prune_verified_backups(source)
    assert fewer.removed == ()
    create_records(store, source, 1)
    exact = store.prune_verified_backups(source)
    assert len(exact.retained) == RETENTION_LIMIT
    assert exact.removed == ()


def test_pruning_failure_is_warning_and_does_not_change_transaction_truth(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path, clock=BackupTestClock())
    create_records(store, source, RETENTION_LIMIT + 1)
    store.unlinker = lambda path: (_ for _ in ()).throw(OSError("prune failure"))
    prune = store.prune_verified_backups(source)
    assert not prune.success
    assert prune.cleanup_error and "prune failure" in prune.cleanup_error

    transaction = SafeSaveTransaction().run(
        TransactionRequest(
            source_path=source,
            baseline=fingerprint_file(source),
            backup_provider=make_store(tmp_path / "transaction"),
            serialize_output=lambda path: path.write_bytes(b"edited"),
            validate_output=lambda path: True,
        )
    )
    assert transaction.success
    assert transaction.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED


def test_successful_transaction_can_be_followed_by_explicit_retention_prune(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path, clock=BackupTestClock())
    create_records(store, source, RETENTION_LIMIT + 1)
    transaction = SafeSaveTransaction().run(
        TransactionRequest(
            source_path=source,
            baseline=fingerprint_file(source),
            backup_provider=store,
            serialize_output=lambda path: path.write_bytes(b"edited"),
            validate_output=lambda path: True,
        )
    )
    assert transaction.success
    prune = store.prune_verified_backups(source)
    assert prune.success
    assert len(prune.retained) == RETENTION_LIMIT
    assert len(prune.removed) == 2


def test_failed_transaction_preserves_new_verified_backup_without_pruning(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path)
    result = SafeSaveTransaction().run(
        TransactionRequest(
            source_path=source,
            baseline=fingerprint_file(source),
            backup_provider=store,
            serialize_output=lambda path: (_ for _ in ()).throw(OSError("save failure")),
            validate_output=lambda path: True,
        )
    )
    assert not result.success
    assert len(store.list_verified_backups(source)[0]) == 1
    assert source.read_bytes() == b"original"


def test_restored_transaction_does_not_prune_verified_backups(tmp_path: Path):
    source = make_source(tmp_path)
    store = make_store(tmp_path)
    create_records(store, source, RETENTION_LIMIT + 1)
    validator = lambda path: path.name != "Level.sav" or path.read_bytes() == b"original"
    from pal_editor.safe_save import ReplacementError, ReplacementOutcome, AtomicReplacer

    class FailOnSecond(AtomicReplacer):
        def __init__(self):
            self.calls = 0

        def replace(self, source_path, replacement_path):
            self.calls += 1
            if self.calls == 2:
                raise ReplacementError("restore failure", ReplacementOutcome.UNKNOWN)
            return super().replace(source_path, replacement_path)

    result = SafeSaveTransaction(replacer=FailOnSecond()).run(
        TransactionRequest(
            source_path=source,
            baseline=fingerprint_file(source),
            backup_provider=store,
            serialize_output=lambda path: path.write_bytes(b"edited"),
            validate_output=validator,
        )
    )
    assert result.recovery_result is RecoveryResult.FAILED
    assert len(store.list_verified_backups(source)[0]) == RETENTION_LIMIT + 2
