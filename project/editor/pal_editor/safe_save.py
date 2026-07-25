"""UI-independent, backup-gated safe-save transaction primitives.

This module is intentionally not connected to :mod:`pal_editor.gui` during
v1.2b Phase 1.  It provides the transaction contract and deterministic file
operations needed by the later production backup provider and Save action.

The replacement primitive is ``os.replace``.  The transaction creates its
temporary files beside the source, so source and replacement are on the same
volume.  Windows provides replacement semantics for a closed destination, but
the transaction still verifies the final bytes and has a recovery path because
filesystem replacement alone is not considered success.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol


class FingerprintError(RuntimeError):
    """Base error for reading a source or backup fingerprint."""


class SourceMissingError(FingerprintError):
    """The requested path does not exist."""


class SourceInaccessibleError(FingerprintError):
    """The requested path is not a readable regular file."""


class FingerprintHashError(FingerprintError):
    """The file could not be hashed."""


class DurabilityError(RuntimeError):
    """A path could not be flushed, fsynced, or closed durably."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


class ReplacementError(RuntimeError):
    """The atomic replacement primitive failed."""

    def __init__(self, message: str, outcome: "ReplacementOutcome") -> None:
        self.outcome = outcome
        super().__init__(message)


class InvalidTransactionState(RuntimeError):
    """A transaction attempted an invalid state transition."""


class TransactionState(str, Enum):
    INITIALIZED = "initialized"
    PREFLIGHT_PASSED = "preflight_passed"
    SOURCE_FINGERPRINT_VERIFIED = "source_fingerprint_verified"
    BACKUP_REQUIRED = "backup_required"
    BACKUP_VERIFIED = "backup_verified"
    TEMPORARY_OUTPUT_WRITTEN = "temporary_output_written"
    TEMPORARY_OUTPUT_DURABLE = "temporary_output_durable"
    TEMPORARY_OUTPUT_VALIDATED = "temporary_output_validated"
    FINAL_SOURCE_FINGERPRINT_VERIFIED = "final_source_fingerprint_verified"
    REPLACEMENT_ATTEMPTED = "replacement_attempted"
    REPLACEMENT_COMPLETED = "replacement_completed"
    REPLACED_SOURCE_VERIFIED = "replaced_source_verified"
    RESTORATION_REQUIRED = "restoration_required"
    RESTORATION_ATTEMPTED = "restoration_attempted"
    RESTORED = "restored"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureStage(str, Enum):
    PREFLIGHT = "preflight"
    SOURCE_FINGERPRINT = "source_fingerprint"
    BACKUP = "backup"
    BACKUP_DURABILITY = "backup_durability"
    TEMPORARY_WRITE = "temporary_write"
    TEMPORARY_DURABILITY = "temporary_durability"
    TEMPORARY_VALIDATION = "temporary_validation"
    FINAL_SOURCE_VERIFICATION = "final_source_verification"
    REPLACEMENT = "replacement"
    POST_REPLACEMENT_VERIFICATION = "post_replacement_verification"
    RESTORATION = "restoration"
    CLEANUP = "cleanup"
    CONCURRENCY = "concurrency"


class FingerprintComparison(str, Enum):
    UNCHANGED = "unchanged"
    METADATA_CHANGED_SAME_CONTENT = "metadata_changed_same_content"
    CONTENT_CHANGED = "content_changed"
    PATH_CHANGED = "path_changed"


class ReplacementOutcome(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    COMPLETED = "completed"
    FAILED_BEFORE_CHANGE = "failed_before_change"
    UNKNOWN = "unknown"


class RecoveryResult(str, Enum):
    NOT_REQUIRED = "not_required"
    NOT_ATTEMPTED = "not_attempted"
    RESTORED = "restored"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class SourceConfidence(str, Enum):
    NOT_VERIFIED = "not_verified"
    ORIGINAL_RESTORED = "original_restored"
    EDITED_SOURCE_VERIFIED = "edited_source_verified"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """On-disk identity and content metadata for one canonical file."""

    path: Path
    size: int
    mtime_ns: int
    sha256: str

    @property
    def canonical_path_key(self) -> str:
        """Return a deterministic comparison key for the supported Windows environment."""

        return os.path.normcase(os.path.normpath(str(self.path)))


def _canonical_path(path: str | Path) -> Path:
    # ``resolve(strict=False)`` follows supported symlink/junction aliases and
    # normalizes ``.`` and ``..`` without requiring the file to exist.
    return Path(path).expanduser().resolve(strict=False)


def fingerprint_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> SourceFingerprint:
    """Read and hash the actual on-disk regular file.

    The content hash is authoritative.  Size and nanosecond mtime are retained
    as fast diagnostic indicators; this helper always hashes so a same-size
    content replacement is detected.
    """

    canonical = _canonical_path(path)
    try:
        stat = canonical.stat()
    except FileNotFoundError as exc:
        raise SourceMissingError(f"Source file does not exist: {canonical}") from exc
    except OSError as exc:
        raise SourceInaccessibleError(f"Source file cannot be inspected: {canonical}: {exc}") from exc
    if not canonical.is_file():
        raise SourceInaccessibleError(f"Source path is not a regular file: {canonical}")

    digest = hashlib.sha256()
    try:
        with canonical.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise SourceMissingError(f"Source file disappeared while hashing: {canonical}") from exc
    except OSError as exc:
        raise FingerprintHashError(f"Could not hash source file {canonical}: {exc}") from exc
    return SourceFingerprint(
        path=canonical,
        size=stat.st_size,
        mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
        sha256=digest.hexdigest(),
    )


def compare_fingerprints(
    expected: SourceFingerprint,
    actual: SourceFingerprint,
) -> FingerprintComparison:
    """Compare path, content, and diagnostic metadata according to policy."""

    if expected.canonical_path_key != actual.canonical_path_key:
        return FingerprintComparison.PATH_CHANGED
    if expected.sha256 != actual.sha256:
        return FingerprintComparison.CONTENT_CHANGED
    if expected.size != actual.size or expected.mtime_ns != actual.mtime_ns:
        return FingerprintComparison.METADATA_CHANGED_SAME_CONTENT
    return FingerprintComparison.UNCHANGED


@dataclass(frozen=True, slots=True)
class BackupProof:
    """Proof returned by a backup provider before replacement is permitted."""

    backup_path: Path
    source_path: Path
    source_fingerprint: SourceFingerprint
    backup_fingerprint: SourceFingerprint
    verified: bool
    durable: bool
    verification_message: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


class BackupProvider(Protocol):
    """The Phase 1 contract for a verified backup provider.

    The production provider, backup root, metadata format, and retention policy
    are intentionally deferred to Phase 2. Tests may provide an isolated fake.
    """

    def create_verified_backup(self, source: SourceFingerprint) -> BackupProof:
        """Create and verify a durable backup for ``source``."""


@dataclass(frozen=True, slots=True)
class ReplacementRecord:
    source_path: Path
    replacement_path: Path
    source_existed_before: bool
    source_exists_after: bool
    replacement_exists_after: bool
    outcome: ReplacementOutcome


class AtomicReplacer:
    """Injectable same-filesystem replacement using ``os.replace``."""

    def replace(self, source_path: Path, replacement_path: Path) -> ReplacementRecord:
        source = _canonical_path(source_path)
        replacement = _canonical_path(replacement_path)
        if source == replacement:
            raise ReplacementError(
                "Replacement path must differ from the active source path",
                ReplacementOutcome.FAILED_BEFORE_CHANGE,
            )
        existed = source.exists()
        try:
            os.replace(str(replacement), str(source))
        except OSError as exc:
            # The caller verifies the source after an exception because the OS
            # cannot always prove from an exception alone whether a replacement
            # crossed its final boundary.
            raise ReplacementError(
                f"Atomic replacement failed for {source}: {exc}",
                ReplacementOutcome.UNKNOWN,
            ) from exc
        return ReplacementRecord(
            source_path=source,
            replacement_path=replacement,
            source_existed_before=existed,
            source_exists_after=source.exists(),
            replacement_exists_after=replacement.exists(),
            outcome=ReplacementOutcome.COMPLETED,
        )


@dataclass(frozen=True, slots=True)
class TransactionRequest:
    """Inputs supplied by a later Save integration without GUI dependencies."""

    source_path: Path
    baseline: SourceFingerprint
    backup_provider: BackupProvider | None
    serialize_output: Callable[[Path], None]
    validate_output: Callable[[Path], object]


@dataclass(frozen=True, slots=True)
class DiagnosticPaths:
    source_path: Path
    temporary_path: Path | None = None
    backup_path: Path | None = None


@dataclass(frozen=True, slots=True)
class TransactionResult:
    """Structured transaction outcome for a future GUI or ledger adapter."""

    transaction_id: str
    started_at: datetime
    completed_at: datetime
    state: TransactionState
    success: bool
    failure_stage: FailureStage | None
    error_message: str | None
    cleanup_error: str | None
    diagnostics: DiagnosticPaths
    baseline_fingerprint: SourceFingerprint
    initial_source_fingerprint: SourceFingerprint | None
    final_pre_replacement_fingerprint: SourceFingerprint | None
    temporary_fingerprint: SourceFingerprint | None
    final_source_fingerprint: SourceFingerprint | None
    backup_proof: BackupProof | None
    replacement_attempted: bool
    replacement_outcome: ReplacementOutcome
    recovery_attempted: bool
    recovery_result: RecoveryResult
    source_confidence: SourceConfidence
    state_history: tuple[TransactionState, ...]


class _TransactionFailure(RuntimeError):
    def __init__(self, stage: FailureStage, message: str) -> None:
        self.stage = stage
        super().__init__(message)


class _RestorationFailure(RuntimeError):
    """Carry the primary restoration failure and cleanup diagnostic separately."""

    def __init__(self, primary_error: Exception, cleanup_error: str | None) -> None:
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error
        super().__init__(str(primary_error))


def durable_path(path: str | Path) -> None:
    """Flush, fsync, and close a completed file path.

    ``os.fsync`` is intentionally not silently skipped.  On a platform where
    it is unsupported or fails, durability is a transaction failure.  The
    caller can report that limitation rather than issuing false backup proof.
    """

    target = _canonical_path(path)
    try:
        handle = target.open("r+b")
    except OSError as exc:
        raise DurabilityError("open", f"Could not open {target} for durability: {exc}") from exc
    try:
        try:
            handle.flush()
        except OSError as exc:
            raise DurabilityError("flush", f"Could not flush {target}: {exc}") from exc
        try:
            os.fsync(handle.fileno())
        except (OSError, NotImplementedError) as exc:
            raise DurabilityError("fsync", f"Could not fsync {target}: {exc}") from exc
    finally:
        try:
            handle.close()
        except OSError as exc:
            raise DurabilityError("close", f"Could not close {target}: {exc}") from exc


def _validation_passed(value: object) -> bool:
    if isinstance(value, bool):
        return value
    valid = getattr(value, "valid", None)
    if valid is not None:
        return bool(valid)
    return True


class SafeSaveTransaction:
    """Run a backup-gated, testable replacement transaction.

    No caller can reach replacement without a verified ``BackupProof`` and a
    final source fingerprint match.  This class does not know whether a GUI
    draft is dirty and never marks one clean.
    """

    _active_lock = threading.Lock()
    _active_sources: set[str] = set()

    _allowed: dict[TransactionState, set[TransactionState]] = {
        TransactionState.INITIALIZED: {TransactionState.PREFLIGHT_PASSED, TransactionState.FAILED},
        TransactionState.PREFLIGHT_PASSED: {
            TransactionState.SOURCE_FINGERPRINT_VERIFIED,
            TransactionState.FAILED,
        },
        TransactionState.SOURCE_FINGERPRINT_VERIFIED: {
            TransactionState.BACKUP_REQUIRED,
            TransactionState.FAILED,
        },
        TransactionState.BACKUP_REQUIRED: {
            TransactionState.BACKUP_VERIFIED,
            TransactionState.FAILED,
        },
        TransactionState.BACKUP_VERIFIED: {
            TransactionState.TEMPORARY_OUTPUT_WRITTEN,
            TransactionState.FAILED,
        },
        TransactionState.TEMPORARY_OUTPUT_WRITTEN: {
            TransactionState.TEMPORARY_OUTPUT_DURABLE,
            TransactionState.FAILED,
        },
        TransactionState.TEMPORARY_OUTPUT_DURABLE: {
            TransactionState.TEMPORARY_OUTPUT_VALIDATED,
            TransactionState.FAILED,
        },
        TransactionState.TEMPORARY_OUTPUT_VALIDATED: {
            TransactionState.FINAL_SOURCE_FINGERPRINT_VERIFIED,
            TransactionState.FAILED,
        },
        TransactionState.FINAL_SOURCE_FINGERPRINT_VERIFIED: {
            TransactionState.REPLACEMENT_ATTEMPTED,
            TransactionState.FAILED,
        },
        TransactionState.REPLACEMENT_ATTEMPTED: {
            TransactionState.REPLACEMENT_COMPLETED,
            TransactionState.RESTORATION_REQUIRED,
            TransactionState.FAILED,
        },
        TransactionState.REPLACEMENT_COMPLETED: {
            TransactionState.REPLACED_SOURCE_VERIFIED,
            TransactionState.RESTORATION_REQUIRED,
        },
        TransactionState.REPLACED_SOURCE_VERIFIED: {TransactionState.COMPLETED, TransactionState.RESTORATION_REQUIRED},
        TransactionState.RESTORATION_REQUIRED: {
            TransactionState.RESTORATION_ATTEMPTED,
            TransactionState.FAILED,
        },
        TransactionState.RESTORATION_ATTEMPTED: {
            TransactionState.RESTORED,
            TransactionState.FAILED,
        },
        TransactionState.RESTORED: set(),
        TransactionState.COMPLETED: set(),
        TransactionState.FAILED: set(),
    }

    def __init__(
        self,
        *,
        fingerprinter: Callable[[str | Path], SourceFingerprint] = fingerprint_file,
        durabilizer: Callable[[str | Path], None] = durable_path,
        replacer: AtomicReplacer | None = None,
        transaction_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.fingerprinter = fingerprinter
        self.durabilizer = durabilizer
        self.replacer = replacer or AtomicReplacer()
        self.transaction_id_factory = transaction_id_factory or (lambda: uuid.uuid4().hex)

    def run(self, request: TransactionRequest) -> TransactionResult:
        started = datetime.now(timezone.utc)
        transaction_id = self.transaction_id_factory()
        source = _canonical_path(request.source_path)
        diagnostics = DiagnosticPaths(source_path=source)
        if not self._claim_source(source):
            return self._failure_result(
                transaction_id,
                started,
                request,
                diagnostics,
                FailureStage.CONCURRENCY,
                f"A safe-save transaction is already active for {source}.",
                state_history=(TransactionState.INITIALIZED, TransactionState.FAILED),
            )

        state_history: list[TransactionState] = [TransactionState.INITIALIZED]
        backup_proof: BackupProof | None = None
        temporary_path: Path | None = None
        initial_source: SourceFingerprint | None = None
        final_pre_source: SourceFingerprint | None = None
        temporary_fingerprint: SourceFingerprint | None = None
        final_source: SourceFingerprint | None = None
        replacement_attempted = False
        replacement_outcome = ReplacementOutcome.NOT_ATTEMPTED
        recovery_attempted = False
        recovery_result = RecoveryResult.NOT_REQUIRED
        cleanup_error: str | None = None
        failure_stage: FailureStage | None = None
        error_message: str | None = None
        source_confidence = SourceConfidence.NOT_VERIFIED

        def transition(next_state: TransactionState) -> None:
            current = state_history[-1]
            if next_state not in self._allowed[current]:
                raise InvalidTransactionState(
                    f"Invalid transaction transition: {current.value} -> {next_state.value}"
                )
            state_history.append(next_state)

        try:
            self._preflight(request, source)
            transition(TransactionState.PREFLIGHT_PASSED)

            initial_source = self._capture_and_compare_baseline(request.baseline, source)
            transition(TransactionState.SOURCE_FINGERPRINT_VERIFIED)
            transition(TransactionState.BACKUP_REQUIRED)

            backup_proof = self._verify_backup(request.backup_provider, request.baseline, source)
            diagnostics = DiagnosticPaths(source, backup_path=_canonical_path(backup_proof.backup_path))
            transition(TransactionState.BACKUP_VERIFIED)

            temporary_path = self._create_temporary_path(source)
            diagnostics = DiagnosticPaths(source, temporary_path=temporary_path, backup_path=diagnostics.backup_path)
            try:
                request.serialize_output(temporary_path)
            except Exception as exc:
                raise _TransactionFailure(FailureStage.TEMPORARY_WRITE, str(exc)) from exc
            if not temporary_path.is_file():
                raise _TransactionFailure(
                    FailureStage.TEMPORARY_WRITE,
                    f"Serializer did not create a regular temporary output: {temporary_path}",
                )
            transition(TransactionState.TEMPORARY_OUTPUT_WRITTEN)

            try:
                self.durabilizer(temporary_path)
            except DurabilityError as exc:
                raise _TransactionFailure(FailureStage.TEMPORARY_DURABILITY, str(exc)) from exc
            except Exception as exc:
                raise _TransactionFailure(FailureStage.TEMPORARY_DURABILITY, str(exc)) from exc
            transition(TransactionState.TEMPORARY_OUTPUT_DURABLE)

            try:
                valid = request.validate_output(temporary_path)
            except Exception as exc:
                raise _TransactionFailure(FailureStage.TEMPORARY_VALIDATION, str(exc)) from exc
            if not _validation_passed(valid):
                raise _TransactionFailure(
                    FailureStage.TEMPORARY_VALIDATION,
                    "Temporary output failed structural validation.",
                )
            try:
                temporary_fingerprint = self.fingerprinter(temporary_path)
            except FingerprintError as exc:
                raise _TransactionFailure(FailureStage.TEMPORARY_VALIDATION, str(exc)) from exc
            transition(TransactionState.TEMPORARY_OUTPUT_VALIDATED)

            try:
                final_pre_source = self._capture_and_compare_baseline(request.baseline, source)
            except _TransactionFailure:
                raise
            transition(TransactionState.FINAL_SOURCE_FINGERPRINT_VERIFIED)

            replacement_attempted = True
            transition(TransactionState.REPLACEMENT_ATTEMPTED)
            try:
                replacement_record = self.replacer.replace(source, temporary_path)
            except ReplacementError as exc:
                replacement_outcome = exc.outcome
                current = self._try_fingerprint(source)
                if current is not None and compare_fingerprints(request.baseline, current) in {
                    FingerprintComparison.UNCHANGED,
                    FingerprintComparison.METADATA_CHANGED_SAME_CONTENT,
                }:
                    replacement_outcome = ReplacementOutcome.FAILED_BEFORE_CHANGE
                    raise _TransactionFailure(FailureStage.REPLACEMENT, str(exc)) from exc
                raise
            replacement_outcome = replacement_record.outcome
            transition(TransactionState.REPLACEMENT_COMPLETED)

            try:
                final_source = self.fingerprinter(source)
                if temporary_fingerprint is None or final_source.sha256 != temporary_fingerprint.sha256:
                    raise ValueError("Replaced source bytes do not match the validated temporary output")
                valid = request.validate_output(source)
                if not _validation_passed(valid):
                    raise ValueError("Replaced source failed structural validation")
            except Exception as exc:
                raise _TransactionFailure(FailureStage.POST_REPLACEMENT_VERIFICATION, str(exc)) from exc
            transition(TransactionState.REPLACED_SOURCE_VERIFIED)
            transition(TransactionState.COMPLETED)
            source_confidence = SourceConfidence.EDITED_SOURCE_VERIFIED
            return self._result(
                transaction_id,
                started,
                request,
                diagnostics,
                state_history,
                success=True,
                failure_stage=None,
                error_message=None,
                cleanup_error=None,
                initial_source=initial_source,
                final_pre_source=final_pre_source,
                temporary_fingerprint=temporary_fingerprint,
                final_source=final_source,
                backup_proof=backup_proof,
                replacement_attempted=replacement_attempted,
                replacement_outcome=replacement_outcome,
                recovery_attempted=False,
                recovery_result=RecoveryResult.NOT_REQUIRED,
                source_confidence=source_confidence,
            )
        except _TransactionFailure as exc:
            failure_stage = exc.stage
            error_message = str(exc)
        except ReplacementError as exc:
            failure_stage = FailureStage.REPLACEMENT
            error_message = str(exc)
            replacement_outcome = exc.outcome
        except (FingerprintError, OSError, ValueError) as exc:
            failure_stage = FailureStage.PREFLIGHT
            error_message = str(exc)
        except Exception as exc:
            failure_stage = failure_stage or FailureStage.PREFLIGHT
            error_message = str(exc)

        if replacement_attempted and backup_proof is not None:
            current = self._try_fingerprint(source)
            unchanged = current is not None and compare_fingerprints(request.baseline, current) in {
                FingerprintComparison.UNCHANGED,
                FingerprintComparison.METADATA_CHANGED_SAME_CONTENT,
            }
            if not unchanged:
                recovery_attempted = True
                try:
                    transition(TransactionState.RESTORATION_REQUIRED)
                    restoration_cleanup_error = self._restore_from_backup(
                        source,
                        backup_proof,
                        request.validate_output,
                        transition,
                    )
                    cleanup_error = self._merge_cleanup_errors(
                        cleanup_error,
                        restoration_cleanup_error,
                    )
                    recovery_result = RecoveryResult.RESTORED
                    source_confidence = SourceConfidence.ORIGINAL_RESTORED
                except _RestorationFailure as exc:
                    recovery_result = RecoveryResult.FAILED
                    source_confidence = SourceConfidence.UNCERTAIN
                    cleanup_error = self._merge_cleanup_errors(
                        cleanup_error,
                        exc.cleanup_error,
                    )
                    error_message = f"{error_message}; restoration failed: {exc.primary_error}"
                    if state_history[-1] != TransactionState.FAILED:
                        transition(TransactionState.FAILED)
                except Exception as exc:
                    recovery_result = RecoveryResult.FAILED
                    source_confidence = SourceConfidence.UNCERTAIN
                    error_message = f"{error_message}; restoration failed: {exc}"
                    if state_history[-1] != TransactionState.FAILED:
                        transition(TransactionState.FAILED)
            else:
                replacement_outcome = ReplacementOutcome.FAILED_BEFORE_CHANGE

        if state_history[-1] not in {TransactionState.RESTORED, TransactionState.FAILED}:
            transition(TransactionState.FAILED)
        cleanup_error = self._merge_cleanup_errors(
            cleanup_error,
            self._cleanup_temporary(temporary_path),
        )
        return self._result(
            transaction_id,
            started,
            request,
            diagnostics,
            state_history,
            success=False,
            failure_stage=failure_stage,
            error_message=error_message or "Safe-save transaction failed.",
            cleanup_error=cleanup_error,
            initial_source=initial_source,
            final_pre_source=final_pre_source,
            temporary_fingerprint=temporary_fingerprint,
            final_source=final_source,
            backup_proof=backup_proof,
            replacement_attempted=replacement_attempted,
            replacement_outcome=replacement_outcome,
            recovery_attempted=recovery_attempted,
            recovery_result=recovery_result,
            source_confidence=source_confidence,
        )

    @classmethod
    def _claim_source(cls, source: Path) -> bool:
        key = os.path.normcase(os.path.normpath(str(source)))
        with cls._active_lock:
            if key in cls._active_sources:
                return False
            cls._active_sources.add(key)
            return True

    @classmethod
    def _release_source(cls, source: Path) -> None:
        key = os.path.normcase(os.path.normpath(str(source)))
        with cls._active_lock:
            cls._active_sources.discard(key)

    def _preflight(self, request: TransactionRequest, source: Path) -> None:
        if request.backup_provider is None:
            raise _TransactionFailure(FailureStage.PREFLIGHT, "A verified backup provider is required")
        if not callable(request.serialize_output):
            raise _TransactionFailure(FailureStage.PREFLIGHT, "A serializer callback is required")
        if not callable(request.validate_output):
            raise _TransactionFailure(FailureStage.PREFLIGHT, "An output-validation callback is required")
        if request.baseline.canonical_path_key != os.path.normcase(os.path.normpath(str(source))):
            raise _TransactionFailure(FailureStage.PREFLIGHT, "Transaction source does not match its baseline path")
        try:
            stat = source.stat()
        except FileNotFoundError as exc:
            raise _TransactionFailure(FailureStage.PREFLIGHT, f"Source file does not exist: {source}") from exc
        except OSError as exc:
            raise _TransactionFailure(FailureStage.PREFLIGHT, f"Source file cannot be accessed: {source}: {exc}") from exc
        if not source.is_file() or stat.st_size < 0:
            raise _TransactionFailure(FailureStage.PREFLIGHT, f"Source is not an accessible regular file: {source}")

    def _capture_and_compare_baseline(
        self,
        baseline: SourceFingerprint,
        source: Path,
    ) -> SourceFingerprint:
        try:
            current = self.fingerprinter(source)
        except FingerprintError as exc:
            raise _TransactionFailure(FailureStage.SOURCE_FINGERPRINT, str(exc)) from exc
        comparison = compare_fingerprints(baseline, current)
        if comparison in {FingerprintComparison.CONTENT_CHANGED, FingerprintComparison.PATH_CHANGED}:
            raise _TransactionFailure(
                FailureStage.SOURCE_FINGERPRINT,
                f"Source changed after loading ({comparison.value}): {source}",
            )
        return current

    def _verify_backup(
        self,
        provider: BackupProvider | None,
        baseline: SourceFingerprint,
        source: Path,
    ) -> BackupProof:
        if provider is None:
            raise _TransactionFailure(FailureStage.BACKUP, "A verified backup provider is required")
        try:
            proof = provider.create_verified_backup(baseline)
        except Exception as exc:
            raise _TransactionFailure(FailureStage.BACKUP, str(exc)) from exc
        if not isinstance(proof, BackupProof):
            raise _TransactionFailure(FailureStage.BACKUP, "Backup provider returned no BackupProof")
        if not proof.verified:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup provider returned an unverified backup")
        if not proof.durable:
            raise _TransactionFailure(FailureStage.BACKUP_DURABILITY, "Backup provider returned a non-durable backup")
        if _canonical_path(proof.source_path) != source:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup represents a different canonical source")
        if proof.source_fingerprint.sha256 != baseline.sha256:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup source fingerprint differs from the transaction baseline")
        try:
            actual_backup = self.fingerprinter(proof.backup_path)
        except FingerprintError as exc:
            raise _TransactionFailure(FailureStage.BACKUP, str(exc)) from exc
        if actual_backup.sha256 != baseline.sha256:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup content hash does not match the source baseline")
        if proof.backup_fingerprint.sha256 != actual_backup.sha256:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup proof fingerprint does not match the backup bytes")
        if proof.backup_fingerprint.canonical_path_key != actual_backup.canonical_path_key:
            raise _TransactionFailure(FailureStage.BACKUP, "Backup proof path does not match the backup file")
        return proof

    @staticmethod
    def _create_temporary_path(source: Path) -> Path:
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{source.stem}.paladmin-",
                suffix=".tmp",
                dir=str(source.parent),
            )
            os.close(descriptor)
            return Path(name).resolve()
        except OSError as exc:
            raise _TransactionFailure(FailureStage.TEMPORARY_WRITE, str(exc)) from exc

    def _restore_from_backup(
        self,
        source: Path,
        proof: BackupProof,
        validate_output: Callable[[Path], object],
        transition: Callable[[TransactionState], None],
    ) -> str | None:
        restore_temp = self._create_temporary_path(source)
        primary_error: Exception | None = None
        try:
            try:
                shutil.copyfile(proof.backup_path, restore_temp)
            except OSError as exc:
                raise _TransactionFailure(FailureStage.RESTORATION, str(exc)) from exc
            try:
                self.durabilizer(restore_temp)
            except Exception as exc:
                raise _TransactionFailure(FailureStage.RESTORATION, str(exc)) from exc
            restored_temp = self.fingerprinter(restore_temp)
            if restored_temp.sha256 != proof.source_fingerprint.sha256:
                raise _TransactionFailure(FailureStage.RESTORATION, "Restoration temporary bytes do not match the verified backup")
            transition(TransactionState.RESTORATION_ATTEMPTED)
            self.replacer.replace(source, restore_temp)
            restored_source = self.fingerprinter(source)
            if restored_source.sha256 != proof.source_fingerprint.sha256:
                raise _TransactionFailure(FailureStage.RESTORATION, "Restored source bytes do not match the verified backup")
            if not _validation_passed(validate_output(source)):
                raise _TransactionFailure(FailureStage.RESTORATION, "Restored source failed structural validation")
            transition(TransactionState.RESTORED)
        except Exception as exc:
            primary_error = exc

        cleanup_error = self._cleanup_temporary(restore_temp)
        if primary_error is not None:
            raise _RestorationFailure(primary_error, cleanup_error) from primary_error
        return cleanup_error

    @staticmethod
    def _try_fingerprint(path: Path) -> SourceFingerprint | None:
        try:
            return fingerprint_file(path)
        except FingerprintError:
            return None

    @staticmethod
    def _cleanup_temporary(path: Path | None) -> str | None:
        if path is None or not path.exists():
            return None
        try:
            path.unlink()
        except OSError as exc:
            return f"Could not clean temporary path {path}: {exc}"
        return None

    @staticmethod
    def _merge_cleanup_errors(
        existing: str | None,
        additional: str | None,
    ) -> str | None:
        if existing and additional:
            return f"{existing}; {additional}"
        return existing or additional

    def _result(
        self,
        transaction_id: str,
        started: datetime,
        request: TransactionRequest,
        diagnostics: DiagnosticPaths,
        state_history: list[TransactionState],
        *,
        success: bool,
        failure_stage: FailureStage | None,
        error_message: str | None,
        cleanup_error: str | None,
        initial_source: SourceFingerprint | None,
        final_pre_source: SourceFingerprint | None,
        temporary_fingerprint: SourceFingerprint | None,
        final_source: SourceFingerprint | None,
        backup_proof: BackupProof | None,
        replacement_attempted: bool,
        replacement_outcome: ReplacementOutcome,
        recovery_attempted: bool,
        recovery_result: RecoveryResult,
        source_confidence: SourceConfidence,
    ) -> TransactionResult:
        source = _canonical_path(request.source_path)
        self._release_source(source)
        return TransactionResult(
            transaction_id=transaction_id,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            state=state_history[-1],
            success=success,
            failure_stage=failure_stage,
            error_message=error_message,
            cleanup_error=cleanup_error,
            diagnostics=diagnostics,
            baseline_fingerprint=request.baseline,
            initial_source_fingerprint=initial_source,
            final_pre_replacement_fingerprint=final_pre_source,
            temporary_fingerprint=temporary_fingerprint,
            final_source_fingerprint=final_source,
            backup_proof=backup_proof,
            replacement_attempted=replacement_attempted,
            replacement_outcome=replacement_outcome,
            recovery_attempted=recovery_attempted,
            recovery_result=recovery_result,
            source_confidence=source_confidence,
            state_history=tuple(state_history),
        )

    def _failure_result(
        self,
        transaction_id: str,
        started: datetime,
        request: TransactionRequest,
        diagnostics: DiagnosticPaths,
        stage: FailureStage,
        message: str,
        *,
        state_history: tuple[TransactionState, ...],
    ) -> TransactionResult:
        source = _canonical_path(request.source_path)
        return TransactionResult(
            transaction_id=transaction_id,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            state=TransactionState.FAILED,
            success=False,
            failure_stage=stage,
            error_message=message,
            cleanup_error=None,
            diagnostics=diagnostics,
            baseline_fingerprint=request.baseline,
            initial_source_fingerprint=None,
            final_pre_replacement_fingerprint=None,
            temporary_fingerprint=None,
            final_source_fingerprint=None,
            backup_proof=None,
            replacement_attempted=False,
            replacement_outcome=ReplacementOutcome.NOT_ATTEMPTED,
            recovery_attempted=False,
            recovery_result=RecoveryResult.NOT_ATTEMPTED,
            source_confidence=SourceConfidence.NOT_VERIFIED,
            state_history=state_history,
        )


@dataclass
class TransactionStateMachine:
    """Small inspectable state-transition helper used by Phase 1 tests/tools.

    ``SafeSaveTransaction`` keeps its transition history in the returned
    result; this helper exposes the same transition contract without touching
    files, GUI state, or a transaction.  It intentionally delegates to the
    transaction's single allowed-transition table so the two contracts cannot
    drift.
    """

    state: TransactionState = TransactionState.INITIALIZED
    history: list[TransactionState] = field(default_factory=lambda: [TransactionState.INITIALIZED])

    def transition(self, next_state: TransactionState) -> None:
        if next_state not in SafeSaveTransaction._allowed[self.state]:
            raise InvalidTransactionState(
                f"Invalid transaction transition: {self.state.value} -> {next_state.value}"
            )
        self.history.append(next_state)
        self.state = next_state


__all__ = [
    "AtomicReplacer",
    "BackupProof",
    "BackupProvider",
    "DiagnosticPaths",
    "DurabilityError",
    "FailureStage",
    "FingerprintComparison",
    "FingerprintError",
    "FingerprintHashError",
    "InvalidTransactionState",
    "RecoveryResult",
    "ReplacementError",
    "ReplacementOutcome",
    "ReplacementRecord",
    "SafeSaveTransaction",
    "SourceConfidence",
    "SourceFingerprint",
    "SourceInaccessibleError",
    "SourceMissingError",
    "TransactionRequest",
    "TransactionResult",
    "TransactionStateMachine",
    "TransactionState",
    "compare_fingerprints",
    "durable_path",
    "fingerprint_file",
]
