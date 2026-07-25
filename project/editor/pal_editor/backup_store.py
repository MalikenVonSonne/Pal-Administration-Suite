"""Production, backup-gated storage for Pal Admin save generations.

Phase 2 deliberately keeps this module separate from Qt and from the GUI.  It
implements the accepted :mod:`pal_editor.safe_save` ``BackupProvider``
contract and exposes retention as an explicit post-success operation.  No
application call site invokes either operation yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import __version__
from .safe_save import (
    BackupProof,
    FingerprintComparison,
    SourceFingerprint,
    compare_fingerprints,
    durable_path,
    fingerprint_file,
)


BACKUP_SCHEMA_VERSION = 1
RETENTION_LIMIT = 5
GROUP_MANIFEST_FILENAME = "source_identity.json"
_SAFE_SUFFIX = re.compile(r"[^A-Za-z0-9._-]+")


class BackupStage(str, Enum):
    ROOT = "root"
    SOURCE_VERIFICATION = "source_verification"
    GROUP = "group"
    TEMPORARY_WRITE = "temporary_write"
    COPY = "copy"
    DURABILITY = "durability"
    FINGERPRINT = "fingerprint"
    FINALIZATION = "finalization"
    METADATA = "metadata"
    VERIFICATION = "verification"
    CLEANUP = "cleanup"


class BackupStoreError(RuntimeError):
    """Structured failure raised before a verified backup proof is returned."""

    def __init__(
        self,
        stage: BackupStage,
        message: str,
        *,
        cleanup_error: str | None = None,
        diagnostics: Mapping[str, str] | None = None,
    ) -> None:
        self.stage = stage
        self.cleanup_error = cleanup_error
        self.diagnostics = dict(diagnostics or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class BackupSourceIdentity:
    """Stable source grouping derived only from the canonical source path."""

    canonical_path: Path
    group_id: str
    original_filename: str

    @classmethod
    def from_path(cls, path: str | Path) -> "BackupSourceIdentity":
        canonical = _canonical_path(path)
        path_key = os.path.normcase(os.path.normpath(str(canonical)))
        digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()
        return cls(
            canonical_path=canonical,
            group_id=f"source-{digest}",
            original_filename=canonical.name,
        )


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    schema_version: int
    canonical_source_path: str
    original_filename: str
    source_group_id: str
    created_at_utc: str
    transaction_id: str
    pal_admin_version: str
    expected_source_fingerprint: SourceFingerprint
    backup_fingerprint: SourceFingerprint
    backup_filename: str
    verification_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canonical_source_path": self.canonical_source_path,
            "original_filename": self.original_filename,
            "source_group_id": self.source_group_id,
            "created_at_utc": self.created_at_utc,
            "transaction_id": self.transaction_id,
            "pal_admin_version": self.pal_admin_version,
            "expected_source_fingerprint": _fingerprint_to_dict(self.expected_source_fingerprint),
            "backup_fingerprint": _fingerprint_to_dict(self.backup_fingerprint),
            "backup_filename": self.backup_filename,
            "verification_status": self.verification_status,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BackupMetadata":
        if payload.get("schema_version") != BACKUP_SCHEMA_VERSION:
            raise ValueError(f"Unsupported backup metadata schema: {payload.get('schema_version')!r}")
        required = (
            "canonical_source_path",
            "original_filename",
            "source_group_id",
            "created_at_utc",
            "transaction_id",
            "pal_admin_version",
            "expected_source_fingerprint",
            "backup_fingerprint",
            "backup_filename",
            "verification_status",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"Backup metadata is missing fields: {', '.join(missing)}")
        _parse_timestamp(str(payload["created_at_utc"]))
        return cls(
            schema_version=BACKUP_SCHEMA_VERSION,
            canonical_source_path=str(payload["canonical_source_path"]),
            original_filename=str(payload["original_filename"]),
            source_group_id=str(payload["source_group_id"]),
            created_at_utc=str(payload["created_at_utc"]),
            transaction_id=str(payload["transaction_id"]),
            pal_admin_version=str(payload["pal_admin_version"]),
            expected_source_fingerprint=_fingerprint_from_dict(payload["expected_source_fingerprint"]),
            backup_fingerprint=_fingerprint_from_dict(payload["backup_fingerprint"]),
            backup_filename=str(payload["backup_filename"]),
            verification_status=str(payload["verification_status"]),
        )


@dataclass(frozen=True, slots=True)
class BackupRecord:
    identity: BackupSourceIdentity
    backup_path: Path
    metadata_path: Path
    metadata: BackupMetadata

    @property
    def created_at(self) -> datetime:
        return _parse_timestamp(self.metadata.created_at_utc)


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    record: BackupRecord
    proof: BackupProof
    transaction_id: str


@dataclass(frozen=True, slots=True)
class PruneResult:
    identity: BackupSourceIdentity
    retained: tuple[BackupRecord, ...]
    removed: tuple[BackupRecord, ...]
    warnings: tuple[str, ...] = ()
    cleanup_error: str | None = None

    @property
    def success(self) -> bool:
        return self.cleanup_error is None


def _canonical_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(_canonical_path(path))))


def _fingerprint_to_dict(fingerprint: SourceFingerprint) -> dict[str, Any]:
    return {
        "path": str(fingerprint.path),
        "size": fingerprint.size,
        "mtime_ns": fingerprint.mtime_ns,
        "sha256": fingerprint.sha256,
    }


def _fingerprint_from_dict(payload: Any) -> SourceFingerprint:
    if not isinstance(payload, Mapping):
        raise ValueError("Backup fingerprint is not an object")
    return SourceFingerprint(
        path=_canonical_path(str(payload["path"])),
        size=int(payload["size"]),
        mtime_ns=int(payload["mtime_ns"]),
        sha256=str(payload["sha256"]),
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Backup timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _default_temp_creator(directory: Path, prefix: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(directory))
    os.close(descriptor)
    return Path(name).resolve()


def _default_directory_creator(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _default_metadata_writer(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()


def _default_renamer(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.rename(str(source), str(destination))


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def default_backup_root() -> Path:
    """Return ``%LOCALAPPDATA%/PalAdmin/Backups`` without creating it."""

    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local_app_data / "PalAdmin" / "Backups").resolve(strict=False)


class BackupStore:
    """Create, verify, and conservatively prune source-identity backups."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        pal_admin_version: str = __version__,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        fingerprinter: Callable[[str | Path], SourceFingerprint] = fingerprint_file,
        durabilizer: Callable[[str | Path], None] = durable_path,
        directory_creator: Callable[[Path], None] = _default_directory_creator,
        temporary_creator: Callable[[Path, str, str], Path] = _default_temp_creator,
        copy_file: Callable[[Path, Path], None] = shutil.copyfile,
        renamer: Callable[[Path, Path], None] = _default_renamer,
        metadata_writer: Callable[[Path, Mapping[str, Any]], None] = _default_metadata_writer,
        metadata_reader: Callable[[Path], Mapping[str, Any]] | None = None,
        unlinker: Callable[[Path], None] = lambda path: path.unlink(),
    ) -> None:
        self.root = _canonical_path(root or default_backup_root())
        self.pal_admin_version = pal_admin_version
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.fingerprinter = fingerprinter
        self.durabilizer = durabilizer
        self.directory_creator = directory_creator
        self.temporary_creator = temporary_creator
        self.copy_file = copy_file
        self.renamer = renamer
        self.metadata_writer = metadata_writer
        self.metadata_reader = metadata_reader or self._read_metadata_file
        self.unlinker = unlinker

    def source_identity(self, source: str | Path | SourceFingerprint) -> BackupSourceIdentity:
        path = source.path if isinstance(source, SourceFingerprint) else source
        return BackupSourceIdentity.from_path(path)

    def group_directory(self, source: str | Path | SourceFingerprint) -> Path:
        identity = self.source_identity(source)
        group = self.root / identity.group_id
        if not _within(group, self.root):
            raise BackupStoreError(BackupStage.GROUP, "Source group escaped the backup root")
        return group

    def create_verified_backup(self, source: SourceFingerprint) -> BackupProof:
        return self.create_backup(source).proof

    def create_backup(
        self,
        source: SourceFingerprint,
        *,
        transaction_id: str | None = None,
    ) -> BackupCreationResult:
        identity = self.source_identity(source)
        temporary_path: Path | None = None
        finalized_backup: Path | None = None
        finalized_metadata: Path | None = None
        cleanup_paths: list[Path] = []
        backup_id = str(transaction_id or self.id_factory())
        try:
            self._ensure_root()
            group = self._ensure_group(identity)
            self._verify_source_baseline(source)
            temporary_path = self._create_temp(group, ".paladmin-backup-", ".tmp")
            cleanup_paths.append(temporary_path)
            try:
                self.copy_file(source.path, temporary_path)
            except Exception as exc:
                raise BackupStoreError(BackupStage.COPY, f"Could not copy source into backup temporary file: {exc}") from exc
            self._durable(temporary_path, BackupStage.DURABILITY)
            try:
                temporary_fingerprint = self.fingerprinter(temporary_path)
            except Exception as exc:
                raise BackupStoreError(BackupStage.FINGERPRINT, f"Could not fingerprint backup temporary file: {exc}") from exc
            if temporary_fingerprint.sha256 != source.sha256:
                raise BackupStoreError(BackupStage.FINGERPRINT, "Backup temporary content does not match the source baseline")
            self._verify_source_baseline(source, stage=BackupStage.SOURCE_VERIFICATION)

            created_at = _utc_timestamp(self.clock())
            backup_name = self._unique_backup_name(source, created_at, backup_id, group)
            finalized_backup = group / backup_name
            self._finalize_exclusive(temporary_path, finalized_backup)
            temporary_path = None
            cleanup_paths.remove(next(path for path in cleanup_paths if path == finalized_backup)) if finalized_backup in cleanup_paths else None
            cleanup_paths.append(finalized_backup)
            try:
                finalized_fingerprint = self.fingerprinter(finalized_backup)
            except Exception as exc:
                raise BackupStoreError(BackupStage.FINGERPRINT, f"Could not fingerprint finalized backup file: {exc}") from exc
            if finalized_fingerprint.sha256 != temporary_fingerprint.sha256:
                raise BackupStoreError(BackupStage.FINGERPRINT, "Finalized backup content does not match its temporary fingerprint")

            metadata_path = group / f"{backup_name}.json"
            metadata = BackupMetadata(
                schema_version=BACKUP_SCHEMA_VERSION,
                canonical_source_path=str(identity.canonical_path),
                original_filename=identity.original_filename,
                source_group_id=identity.group_id,
                created_at_utc=created_at,
                transaction_id=backup_id,
                pal_admin_version=self.pal_admin_version,
                expected_source_fingerprint=source,
                backup_fingerprint=finalized_fingerprint,
                backup_filename=backup_name,
                verification_status="verified",
            )
            self._write_json_exclusive(metadata_path, metadata.to_dict())
            finalized_metadata = metadata_path
            cleanup_paths.append(metadata_path)
            record = self.verify_record(metadata_path)
            proof = BackupProof(
                backup_path=record.backup_path,
                source_path=identity.canonical_path,
                source_fingerprint=source,
                backup_fingerprint=record.metadata.backup_fingerprint,
                verified=True,
                durable=True,
                verification_message="Backup data and metadata verified",
                metadata={
                    "metadata_path": str(record.metadata_path),
                    "source_group_id": identity.group_id,
                    "transaction_id": backup_id,
                },
            )
            cleanup_paths.clear()
            return BackupCreationResult(record=record, proof=proof, transaction_id=backup_id)
        except BackupStoreError as exc:
            cleanup_error = self._cleanup_paths(cleanup_paths)
            if exc.cleanup_error is None and cleanup_error is not None:
                raise BackupStoreError(exc.stage, str(exc), cleanup_error=cleanup_error, diagnostics=exc.diagnostics) from exc
            raise
        except Exception as exc:
            cleanup_error = self._cleanup_paths(cleanup_paths)
            raise BackupStoreError(
                BackupStage.METADATA if finalized_backup is not None else BackupStage.TEMPORARY_WRITE,
                str(exc),
                cleanup_error=cleanup_error,
                diagnostics={
                    "source_path": str(identity.canonical_path),
                    "backup_path": str(finalized_backup) if finalized_backup else "",
                    "metadata_path": str(finalized_metadata) if finalized_metadata else "",
                },
            ) from exc

    def verify_record(self, metadata_path: str | Path) -> BackupRecord:
        metadata_file = _canonical_path(metadata_path)
        if metadata_file.name == GROUP_MANIFEST_FILENAME:
            raise BackupStoreError(BackupStage.VERIFICATION, "Source identity manifest is not a backup record")
        if not _within(metadata_file, self.root):
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup metadata escaped the backup root")
        try:
            payload = self.metadata_reader(metadata_file)
            metadata = BackupMetadata.from_dict(payload)
        except Exception as exc:
            raise BackupStoreError(BackupStage.VERIFICATION, f"Backup metadata is invalid: {exc}") from exc
        identity = BackupSourceIdentity.from_path(metadata.canonical_source_path)
        group = self.root / metadata.source_group_id
        if metadata.source_group_id != identity.group_id:
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup metadata source group does not match its canonical path")
        if metadata_file.parent != group:
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup metadata is in the wrong source group")
        backup_path = _canonical_path(group / metadata.backup_filename)
        if not _within(backup_path, group) or backup_path.name != metadata.backup_filename:
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup data path escaped its source group")
        if metadata.expected_source_fingerprint.canonical_path_key != _path_key(identity.canonical_path):
            raise BackupStoreError(BackupStage.VERIFICATION, "Expected source fingerprint belongs to another source")
        if metadata.backup_fingerprint.canonical_path_key != _path_key(backup_path):
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup fingerprint belongs to another file")
        try:
            actual = self.fingerprinter(backup_path)
        except Exception as exc:
            raise BackupStoreError(BackupStage.VERIFICATION, f"Backup data is missing or inaccessible: {exc}") from exc
        if actual.sha256 != metadata.backup_fingerprint.sha256:
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup data does not match its metadata fingerprint")
        if actual.sha256 != metadata.expected_source_fingerprint.sha256:
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup data does not match its expected source fingerprint")
        if metadata.verification_status != "verified":
            raise BackupStoreError(BackupStage.VERIFICATION, "Backup metadata is not marked verified")
        return BackupRecord(identity=identity, backup_path=backup_path, metadata_path=metadata_file, metadata=metadata)

    def list_verified_backups(
        self,
        source: str | Path | SourceFingerprint,
    ) -> tuple[tuple[BackupRecord, ...], tuple[str, ...]]:
        identity = self.source_identity(source)
        group = self.root / identity.group_id
        if not group.is_dir():
            return (), ()
        records: list[BackupRecord] = []
        warnings: list[str] = []
        manifest = group / GROUP_MANIFEST_FILENAME
        if not manifest.is_file():
            return (), (f"Preserved source group without identity manifest: {group}",)
        try:
            payload = self.metadata_reader(manifest)
            if payload.get("schema_version") != BACKUP_SCHEMA_VERSION:
                raise ValueError("unsupported source identity schema")
            if payload.get("source_group_id") != identity.group_id or _canonical_path(payload.get("canonical_source_path", "")) != identity.canonical_path:
                raise ValueError("source identity does not match requested source")
        except Exception as exc:
            return (), (f"Preserved source group with invalid identity manifest {manifest}: {exc}",)
        entries = sorted(group.iterdir(), key=lambda path: path.name.casefold())
        for entry in entries:
            if entry.name == GROUP_MANIFEST_FILENAME:
                continue
            if entry.suffix.casefold() == ".json":
                try:
                    record = self.verify_record(entry)
                    if record.identity.canonical_path != identity.canonical_path:
                        raise BackupStoreError(BackupStage.VERIFICATION, "Backup belongs to another source")
                    records.append(record)
                except BackupStoreError as exc:
                    warnings.append(f"Preserved unrecognized or damaged record {entry}: {exc}")
        known_data = {record.backup_path for record in records}
        for entry in entries:
            if entry.name == GROUP_MANIFEST_FILENAME or entry.suffix.casefold() == ".json":
                continue
            if entry in known_data or entry.name.startswith(".paladmin-"):
                continue
            if entry.is_file():
                warnings.append(f"Preserved unrecognized backup-group file: {entry}")
        return tuple(records), tuple(warnings)

    def prune_verified_backups(
        self,
        source: str | Path | SourceFingerprint,
    ) -> PruneResult:
        identity = self.source_identity(source)
        records, warnings = self.list_verified_backups(source)
        ordered = tuple(sorted(records, key=lambda record: (record.created_at, record.backup_path.name), reverse=True))
        retained = ordered[:RETENTION_LIMIT]
        removed: list[BackupRecord] = []
        cleanup_errors: list[str] = []
        for record in ordered[RETENTION_LIMIT:]:
            pair_errors: list[str] = []
            for path in (record.backup_path, record.metadata_path):
                try:
                    self.unlinker(path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    pair_errors.append(f"Could not remove {path}: {exc}")
            if pair_errors:
                cleanup_errors.extend(pair_errors)
            elif not record.backup_path.exists() and not record.metadata_path.exists():
                removed.append(record)
        return PruneResult(
            identity=identity,
            retained=retained,
            removed=tuple(removed),
            warnings=warnings,
            cleanup_error="; ".join(cleanup_errors) if cleanup_errors else None,
        )

    def _ensure_root(self) -> None:
        try:
            self.directory_creator(self.root)
        except Exception as exc:
            raise BackupStoreError(BackupStage.ROOT, f"Could not create backup root {self.root}: {exc}") from exc

    def _ensure_group(self, identity: BackupSourceIdentity) -> Path:
        group = self.group_directory(identity.canonical_path)
        try:
            self.directory_creator(group)
        except Exception as exc:
            raise BackupStoreError(BackupStage.GROUP, f"Could not create source backup group {group}: {exc}") from exc
        manifest = group / GROUP_MANIFEST_FILENAME
        if manifest.exists():
            try:
                payload = self.metadata_reader(manifest)
                if payload.get("schema_version") != BACKUP_SCHEMA_VERSION:
                    raise ValueError("unsupported source identity schema")
                if payload.get("source_group_id") != identity.group_id or _canonical_path(payload.get("canonical_source_path", "")) != identity.canonical_path:
                    raise ValueError("source identity collision")
            except Exception as exc:
                raise BackupStoreError(BackupStage.GROUP, f"Source group identity is invalid: {exc}") from exc
        elif any(group.iterdir()):
            raise BackupStoreError(BackupStage.GROUP, "Source group contains files but has no identity manifest")
        else:
            self._write_json_exclusive(
                manifest,
                {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "canonical_source_path": str(identity.canonical_path),
                    "source_group_id": identity.group_id,
                },
            )
        return group

    def _verify_source_baseline(
        self,
        expected: SourceFingerprint,
        *,
        stage: BackupStage = BackupStage.SOURCE_VERIFICATION,
    ) -> SourceFingerprint:
        try:
            current = self.fingerprinter(expected.path)
        except Exception as exc:
            raise BackupStoreError(stage, f"Source is missing or inaccessible: {exc}") from exc
        comparison = compare_fingerprints(expected, current)
        if comparison in {FingerprintComparison.CONTENT_CHANGED, FingerprintComparison.PATH_CHANGED}:
            raise BackupStoreError(stage, f"Source no longer matches the expected baseline: {comparison.value}")
        return current

    def _create_temp(self, group: Path, prefix: str, suffix: str) -> Path:
        try:
            path = _canonical_path(self.temporary_creator(group, prefix, suffix))
        except Exception as exc:
            raise BackupStoreError(BackupStage.TEMPORARY_WRITE, f"Could not create backup temporary file: {exc}") from exc
        if not _within(path, group):
            raise BackupStoreError(BackupStage.TEMPORARY_WRITE, "Backup temporary file escaped its source group")
        return path

    def _durable(self, path: Path, stage: BackupStage) -> None:
        try:
            self.durabilizer(path)
        except Exception as exc:
            raise BackupStoreError(stage, f"Backup path could not be made durable: {exc}") from exc

    def _unique_backup_name(self, source: SourceFingerprint, created_at: str, backup_id: str, group: Path) -> str:
        extension = source.path.suffix or ".sav"
        safe_extension = _SAFE_SUFFIX.sub("", extension)
        if not safe_extension.startswith("."):
            safe_extension = ".sav"
        timestamp = _SAFE_SUFFIX.sub("-", created_at).strip("-")
        safe_id = _SAFE_SUFFIX.sub("-", backup_id).strip("-") or uuid.uuid4().hex
        base = f"backup-{timestamp}-{safe_id}"
        candidate = f"{base}{safe_extension}"
        counter = 1
        while (group / candidate).exists() or (group / f"{candidate}.json").exists():
            candidate = f"{base}-{counter}{safe_extension}"
            counter += 1
        return candidate

    def _finalize_exclusive(self, temporary: Path, final: Path) -> None:
        if final.exists():
            raise BackupStoreError(BackupStage.FINALIZATION, f"Backup destination already exists: {final}")
        try:
            self.renamer(temporary, final)
        except Exception as exc:
            raise BackupStoreError(BackupStage.FINALIZATION, f"Could not finalize backup file: {exc}") from exc

    def _write_json_exclusive(self, final: Path, payload: Mapping[str, Any]) -> None:
        if final.exists():
            raise BackupStoreError(BackupStage.METADATA, f"Metadata destination already exists: {final}")
        temporary = self._create_temp(final.parent, f".{final.name}-", ".tmp")
        cleanup_error: str | None = None
        try:
            try:
                self.metadata_writer(temporary, payload)
            except Exception as exc:
                raise BackupStoreError(BackupStage.METADATA, f"Could not write backup metadata: {exc}") from exc
            self._durable(temporary, BackupStage.DURABILITY)
            self._finalize_exclusive(temporary, final)
            temporary = None  # type: ignore[assignment]
        except BackupStoreError as exc:
            cleanup_error = self._cleanup_paths([temporary] if temporary is not None else [])
            if cleanup_error and exc.cleanup_error is None:
                raise BackupStoreError(exc.stage, str(exc), cleanup_error=cleanup_error, diagnostics=exc.diagnostics) from exc
            raise
        finally:
            if temporary is not None:
                self._cleanup_paths([temporary])

    def _read_metadata_file(self, path: Path) -> Mapping[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("metadata root must be an object")
        return payload

    def _cleanup_paths(self, paths: Iterable[Path]) -> str | None:
        errors: list[str] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                self.unlinker(path)
            except OSError as exc:
                errors.append(f"Could not clean {path}: {exc}")
        return "; ".join(errors) if errors else None


__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "GROUP_MANIFEST_FILENAME",
    "RETENTION_LIMIT",
    "BackupCreationResult",
    "BackupMetadata",
    "BackupRecord",
    "BackupSourceIdentity",
    "BackupStage",
    "BackupStore",
    "BackupStoreError",
    "PruneResult",
    "default_backup_root",
]
