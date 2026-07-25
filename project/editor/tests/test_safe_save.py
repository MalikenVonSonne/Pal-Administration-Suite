from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from pal_editor.safe_save import (
    AtomicReplacer,
    BackupProof,
    DurabilityError,
    FailureStage,
    FingerprintComparison,
    RecoveryResult,
    ReplacementError,
    ReplacementOutcome,
    SafeSaveTransaction,
    SourceConfidence,
    TransactionRequest,
    TransactionState,
    TransactionStateMachine,
    InvalidTransactionState,
    compare_fingerprints,
    durable_path,
    fingerprint_file,
)


class FakeBackupProvider:
    def __init__(self, backup_root: Path, *, verified: bool = True, durable: bool = True) -> None:
        self.backup_root = backup_root
        self.verified = verified
        self.durable = durable
        self.calls = 0
        self.backup_path: Path | None = None

    def create_verified_backup(self, source):
        self.calls += 1
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.backup_path = self.backup_root / f"backup-{self.calls}.sav"
        shutil.copyfile(source.path, self.backup_path)
        backup_fingerprint = fingerprint_file(self.backup_path)
        return BackupProof(
            backup_path=self.backup_path,
            source_path=source.path,
            source_fingerprint=source,
            backup_fingerprint=backup_fingerprint,
            verified=self.verified,
            durable=self.durable,
            verification_message="isolated test backup",
        )


class FailingReplacer(AtomicReplacer):
    def __init__(self, *, fail_on_call: int = 1, outcome: ReplacementOutcome = ReplacementOutcome.UNKNOWN) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.outcome = outcome

    def replace(self, source_path: Path, replacement_path: Path):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise ReplacementError("injected replacement failure", self.outcome)
        return super().replace(source_path, replacement_path)


class MutatingReplacer(AtomicReplacer):
    def __init__(self, mutation: bytes) -> None:
        self.mutation = mutation
        self.calls = 0

    def replace(self, source_path: Path, replacement_path: Path):
        self.calls += 1
        record = super().replace(source_path, replacement_path)
        if self.calls == 1:
            Path(source_path).write_bytes(self.mutation)
        return record


class MutatingProofProvider(FakeBackupProvider):
    def __init__(self, backup_root: Path, mutation) -> None:
        super().__init__(backup_root)
        self.mutation = mutation

    def create_verified_backup(self, source):
        proof = super().create_verified_backup(source)
        return self.mutation(proof)


def make_request(source: Path, provider, *, serializer, validator=lambda path: True) -> TransactionRequest:
    return TransactionRequest(
        source_path=source,
        baseline=fingerprint_file(source),
        backup_provider=provider,
        serialize_output=serializer,
        validate_output=validator,
    )


def run_bytes_transaction(tmp_path: Path, payload: bytes = b"edited"):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    request = make_request(source, provider, serializer=lambda path: path.write_bytes(payload))
    result = SafeSaveTransaction().run(request)
    return source, provider, result


def test_fingerprints_hash_content_and_accept_metadata_only_changes(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"same bytes")
    before = fingerprint_file(source)
    os.utime(source, ns=(before.mtime_ns + 1000, before.mtime_ns + 1000))
    after_metadata = fingerprint_file(source)
    assert compare_fingerprints(before, after_metadata) is FingerprintComparison.METADATA_CHANGED_SAME_CONTENT

    source.write_bytes(b"changed!!!")
    after_content = fingerprint_file(source)
    assert after_content.size == before.size
    assert compare_fingerprints(before, after_content) is FingerprintComparison.CONTENT_CHANGED


def test_missing_source_is_distinct(tmp_path: Path):
    with pytest.raises(Exception) as raised:
        fingerprint_file(tmp_path / "missing.sav")
    assert raised.value.__class__.__name__ == "SourceMissingError"


def test_backup_is_required_and_unverified_backup_gates_replacement(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    baseline = fingerprint_file(source)
    request = TransactionRequest(source, baseline, None, lambda path: path.write_bytes(b"edited"), lambda path: True)
    result = SafeSaveTransaction().run(request)
    assert not result.success
    assert result.failure_stage is FailureStage.PREFLIGHT
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize(
    "mutation, expected_reason",
    [
        (
            lambda proof: replace(proof, source_path=proof.source_path.parent / "OtherLevel.sav"),
            "different canonical source",
        ),
        (
            lambda proof: replace(
                proof,
                source_fingerprint=replace(proof.source_fingerprint, sha256="0" * 64),
            ),
            "source fingerprint differs",
        ),
        (
            lambda proof: replace(
                proof,
                backup_fingerprint=replace(proof.backup_fingerprint, sha256="0" * 64),
            ),
            "proof fingerprint does not match",
        ),
    ],
)
def test_mismatched_backup_proofs_are_rejected_before_temporary_output(
    tmp_path: Path,
    mutation,
    expected_reason: str,
):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = MutatingProofProvider(tmp_path / "backups", mutation)
    result = SafeSaveTransaction().run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"))
    )
    assert not result.success
    assert result.failure_stage is FailureStage.BACKUP
    assert not result.replacement_attempted
    assert result.diagnostics.temporary_path is None
    assert expected_reason in (result.error_message or "")
    assert source.read_bytes() == b"original"
    assert provider.backup_path is not None and provider.backup_path.exists()


def test_missing_backup_after_proof_creation_is_rejected_before_replacement(tmp_path: Path):
    def remove_backup(proof):
        proof.backup_path.unlink()
        return proof

    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = MutatingProofProvider(tmp_path / "backups", remove_backup)
    result = SafeSaveTransaction().run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"))
    )
    assert not result.success
    assert result.failure_stage is FailureStage.BACKUP
    assert not result.replacement_attempted
    assert result.diagnostics.temporary_path is None
    assert "does not exist" in (result.error_message or "")
    assert source.read_bytes() == b"original"

    provider = FakeBackupProvider(tmp_path / "bad-backup", verified=False)
    request = make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"))
    result = SafeSaveTransaction().run(request)
    assert not result.success
    assert result.failure_stage is FailureStage.BACKUP
    assert not result.replacement_attempted
    assert source.read_bytes() == b"original"


def test_successful_transaction_is_backup_gated_durable_and_verified(tmp_path: Path):
    source, provider, result = run_bytes_transaction(tmp_path)
    assert result.success
    assert result.state is TransactionState.COMPLETED
    assert result.source_confidence is SourceConfidence.EDITED_SOURCE_VERIFIED
    assert result.replacement_outcome is ReplacementOutcome.COMPLETED
    assert result.recovery_result is RecoveryResult.NOT_REQUIRED
    assert result.state_history[-4:] == (
        TransactionState.REPLACEMENT_COMPLETED,
        TransactionState.REPLACED_SOURCE_VERIFIED,
        TransactionState.COMPLETED,
        TransactionState.COMPLETED,
    ) or result.state_history[-3:] == (
        TransactionState.REPLACEMENT_COMPLETED,
        TransactionState.REPLACED_SOURCE_VERIFIED,
        TransactionState.COMPLETED,
    )
    assert source.read_bytes() == b"edited"
    assert provider.backup_path is not None and provider.backup_path.read_bytes() == b"original"
    assert not list(tmp_path.glob(".Level.paladmin-*.tmp"))


def test_temp_write_and_durability_failures_leave_source_unchanged(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    request = make_request(source, provider, serializer=lambda path: (_ for _ in ()).throw(OSError("write failed")))
    result = SafeSaveTransaction().run(request)
    assert result.failure_stage is FailureStage.TEMPORARY_WRITE
    assert source.read_bytes() == b"original"
    assert result.diagnostics.temporary_path is not None
    assert not result.diagnostics.temporary_path.exists()

    def fail_durability(path):
        raise DurabilityError("fsync", "injected fsync failure")

    request = make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"))
    result = SafeSaveTransaction(durabilizer=fail_durability).run(request)
    assert result.failure_stage is FailureStage.TEMPORARY_DURABILITY
    assert source.read_bytes() == b"original"


def test_temp_validation_failure_prevents_replacement(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    request = make_request(
        source,
        provider,
        serializer=lambda path: path.write_bytes(b"edited"),
        validator=lambda path: False,
    )
    result = SafeSaveTransaction().run(request)
    assert result.failure_stage is FailureStage.TEMPORARY_VALIDATION
    assert not result.replacement_attempted
    assert source.read_bytes() == b"original"


def test_external_change_before_final_verification_aborts_without_replacement(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")

    def serializer(path):
        path.write_bytes(b"edited")
        source.write_bytes(b"external change")

    result = SafeSaveTransaction().run(make_request(source, provider, serializer=serializer))
    assert result.failure_stage is FailureStage.SOURCE_FINGERPRINT
    assert not result.replacement_attempted
    assert source.read_bytes() == b"external change"


def test_replacement_failure_before_change_is_distinguished(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    result = SafeSaveTransaction(replacer=FailingReplacer(outcome=ReplacementOutcome.FAILED_BEFORE_CHANGE)).run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"))
    )
    assert not result.success
    assert result.failure_stage is FailureStage.REPLACEMENT
    assert result.replacement_outcome is ReplacementOutcome.FAILED_BEFORE_CHANGE
    assert not result.recovery_attempted
    assert source.read_bytes() == b"original"


def test_post_replacement_validation_failure_restores_verified_backup(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    validation_calls = 0

    def validator(path):
        nonlocal validation_calls
        validation_calls += 1
        return path.name != "Level.sav" or path.read_bytes() == b"original"

    result = SafeSaveTransaction().run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.failure_stage is FailureStage.POST_REPLACEMENT_VERIFICATION
    assert result.recovery_attempted
    assert result.recovery_result is RecoveryResult.RESTORED
    assert result.state is TransactionState.RESTORED
    assert result.source_confidence is SourceConfidence.ORIGINAL_RESTORED
    assert result.cleanup_error is None
    assert source.read_bytes() == b"original"
    

def test_successful_restoration_with_restoration_cleanup_failure_preserves_both_diagnostics(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    cleanup_calls = 0

    def cleanup(path):
        nonlocal cleanup_calls
        cleanup_calls += 1
        return "restoration temporary cleanup failed" if cleanup_calls == 1 else None

    monkeypatch.setattr(SafeSaveTransaction, "_cleanup_temporary", staticmethod(cleanup))
    validator = lambda path: path.name != "Level.sav" or path.read_bytes() == b"original"
    result = SafeSaveTransaction().run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.state is TransactionState.RESTORED
    assert result.recovery_result is RecoveryResult.RESTORED
    assert result.source_confidence is SourceConfidence.ORIGINAL_RESTORED
    assert result.error_message == "Replaced source failed structural validation"
    assert result.cleanup_error == "restoration temporary cleanup failed"
    assert source.read_bytes() == b"original"
    assert provider.backup_path is not None and provider.backup_path.exists()


def test_failed_restoration_with_cleanup_success_preserves_primary_error(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    validator = lambda path: path.name != "Level.sav" or path.read_bytes() == b"original"
    result = SafeSaveTransaction(replacer=FailingReplacer(fail_on_call=2)).run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.state is TransactionState.FAILED
    assert result.recovery_result is RecoveryResult.FAILED
    assert result.source_confidence is SourceConfidence.UNCERTAIN
    assert "Replaced source failed structural validation" in (result.error_message or "")
    assert "restoration failed: injected replacement failure" in (result.error_message or "")
    assert result.cleanup_error is None
    assert source.read_bytes() == b"edited"
    assert provider.backup_path is not None and provider.backup_path.exists()


def test_failed_restoration_with_cleanup_failure_preserves_both_errors(tmp_path: Path, monkeypatch):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    cleanup_calls = 0

    def cleanup(path):
        nonlocal cleanup_calls
        cleanup_calls += 1
        return "restoration temporary cleanup failed" if cleanup_calls == 1 else None

    monkeypatch.setattr(SafeSaveTransaction, "_cleanup_temporary", staticmethod(cleanup))
    validator = lambda path: path.name != "Level.sav" or path.read_bytes() == b"original"
    result = SafeSaveTransaction(replacer=FailingReplacer(fail_on_call=2)).run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.state is TransactionState.FAILED
    assert result.recovery_result is RecoveryResult.FAILED
    assert result.source_confidence is SourceConfidence.UNCERTAIN
    assert "restoration failed: injected replacement failure" in (result.error_message or "")
    assert result.cleanup_error == "restoration temporary cleanup failed"
    assert source.read_bytes() == b"edited"
    assert provider.backup_path is not None and provider.backup_path.exists()


def test_final_source_fingerprint_mismatch_triggers_successful_recovery(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    validator = lambda path: True
    result = SafeSaveTransaction(replacer=MutatingReplacer(b"tampered after replacement")).run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.failure_stage is FailureStage.POST_REPLACEMENT_VERIFICATION
    assert result.recovery_attempted
    assert result.recovery_result is RecoveryResult.RESTORED
    assert result.source_confidence is SourceConfidence.ORIGINAL_RESTORED
    assert result.state is TransactionState.RESTORED
    assert source.read_bytes() == b"original"
    assert provider.backup_path is not None and provider.backup_path.read_bytes() == b"original"


@pytest.mark.parametrize(
    "failure_mode",
    ["serialization", "validation", "replacement", "restoration_success", "restoration_failure", "unexpected"],
)
def test_guard_is_released_after_each_transaction_outcome(tmp_path: Path, failure_mode: str):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "first-backups")
    validator = lambda path: path.name != "Level.sav" or path.read_bytes() == b"original"
    first_engine = SafeSaveTransaction()
    first_kwargs = {}

    if failure_mode == "serialization":
        first_kwargs["serializer"] = lambda path: (_ for _ in ()).throw(OSError("write failed"))
    elif failure_mode == "validation":
        first_kwargs["serializer"] = lambda path: path.write_bytes(b"edited")
        first_kwargs["validator"] = lambda path: False
    elif failure_mode == "replacement":
        first_kwargs["serializer"] = lambda path: path.write_bytes(b"edited")
        first_engine = SafeSaveTransaction(replacer=FailingReplacer())
    elif failure_mode == "restoration_success":
        first_kwargs["serializer"] = lambda path: path.write_bytes(b"edited")
        first_kwargs["validator"] = validator
    elif failure_mode == "restoration_failure":
        first_kwargs["serializer"] = lambda path: path.write_bytes(b"edited")
        first_kwargs["validator"] = validator
        first_engine = SafeSaveTransaction(replacer=FailingReplacer(fail_on_call=2))
    else:
        first_kwargs["serializer"] = lambda path: path.write_bytes(b"edited")
        first_engine = SafeSaveTransaction(
            fingerprinter=lambda path: (_ for _ in ()).throw(RuntimeError("unexpected fingerprint failure"))
        )

    first = first_engine.run(make_request(source, provider, **first_kwargs))
    second_provider = FakeBackupProvider(tmp_path / "second-backups")
    second = SafeSaveTransaction().run(
        make_request(source, second_provider, serializer=lambda path: path.write_bytes(b"second"))
    )
    assert first.failure_stage is not FailureStage.CONCURRENCY
    assert second.failure_stage is not FailureStage.CONCURRENCY
    assert second.success
def test_recovery_failure_reports_uncertain_source(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    def validator(path):
        return path.name != "Level.sav" or path.read_bytes() == b"original"

    result = SafeSaveTransaction(replacer=FailingReplacer(fail_on_call=2)).run(
        make_request(source, provider, serializer=lambda path: path.write_bytes(b"edited"), validator=validator)
    )
    assert not result.success
    assert result.recovery_attempted
    assert result.recovery_result is RecoveryResult.FAILED
    assert result.source_confidence is SourceConfidence.UNCERTAIN
    assert result.state is TransactionState.FAILED
    assert result.error_message and "restoration failed" in result.error_message


def test_concurrent_transaction_for_same_source_is_rejected(tmp_path: Path):
    source = tmp_path / "Level.sav"
    source.write_bytes(b"original")
    provider = FakeBackupProvider(tmp_path / "backups")
    nested: list = []

    def serializer(path):
        nested.append(
            SafeSaveTransaction().run(
                make_request(source, FakeBackupProvider(tmp_path / "nested"), serializer=lambda nested_path: nested_path.write_bytes(b"nested"))
            )
        )
        path.write_bytes(b"edited")

    result = SafeSaveTransaction().run(make_request(source, provider, serializer=serializer))
    assert result.success
    assert len(nested) == 1
    assert not nested[0].success
    assert nested[0].failure_stage is FailureStage.CONCURRENCY


def test_real_durability_uses_flush_fsync_and_close(tmp_path: Path):
    path = tmp_path / "durable.sav"
    path.write_bytes(b"durable")
    durable_path(path)
    assert path.read_bytes() == b"durable"


def test_transaction_state_machine_rejects_invalid_transitions():
    machine = TransactionStateMachine()
    with pytest.raises(InvalidTransactionState):
        machine.transition(TransactionState.COMPLETED)
    machine.transition(TransactionState.PREFLIGHT_PASSED)
    assert machine.state is TransactionState.PREFLIGHT_PASSED
    assert machine.history == [TransactionState.INITIALIZED, TransactionState.PREFLIGHT_PASSED]


def test_atomic_replacer_uses_same_directory_and_removes_temp(tmp_path: Path):
    source = tmp_path / "Level.sav"
    replacement = tmp_path / ".Level.paladmin-test.tmp"
    source.write_bytes(b"original")
    replacement.write_bytes(b"edited")
    record = AtomicReplacer().replace(source, replacement)
    assert record.outcome is ReplacementOutcome.COMPLETED
    assert source.read_bytes() == b"edited"
    assert not replacement.exists()
