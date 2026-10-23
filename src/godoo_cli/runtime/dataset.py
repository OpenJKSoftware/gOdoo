"""Resolve the fixed runtime dataset layout used by downstream projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .restore import DumpIdentity, RuntimeRestoreError, custom_dump_identity, validate_custom_dump, validate_filestore

DatasetKind = Literal["custom", "archive"]


@dataclass(frozen=True)
class RuntimeDataset:
    """A resolved dataset source selected before runtime lifecycle work."""

    kind: DatasetKind
    source: Path
    original_filestore: Path | None = None
    dump_identity: DumpIdentity | None = None


def _plain_filestore(dataset_root: Path) -> Path:
    """Return the source-database filestore from a fixed dataset root."""
    return dataset_root / "odoo_filestore" / "filestore" / "odoo"


def resolve_runtime_dataset(
    *,
    dataset_root: Path | None,
    seed: Path | None,
    original_filestore: Path | None,
) -> RuntimeDataset | None:
    """Choose and validate one runtime seed without changing runtime state.

    An explicit seed remains authoritative. A dataset root selects its plain
    custom dump by default. An explicitly selected paired upgraded ZIP infers
    the original filestore.

    Returns:
        The selected source, or None when neither a dataset nor a seed was supplied.

    Raises:
        ValueError: If the dataset directory does not exist.
        RuntimeRestoreError: If a dump or filestore is invalid, or the dump changes during validation.
    """
    if seed is not None:
        if dataset_root is None or not dataset_root.is_dir():
            return RuntimeDataset("archive", seed, original_filestore)
        dataset_root = dataset_root.resolve()
        upgraded = dataset_root / "upgraded.zip"
        inferred_original = _plain_filestore(dataset_root) if seed.resolve() == upgraded else None
        return RuntimeDataset("archive", seed, original_filestore or inferred_original)

    if dataset_root is None:
        return None

    if not dataset_root.is_dir():
        message = f"Runtime dataset directory does not exist: {dataset_root}"
        raise ValueError(message)

    dataset_root = dataset_root.resolve()
    plain_dump = dataset_root / "odoo.dump"
    plain_filestore = _plain_filestore(dataset_root)

    dump_identity = custom_dump_identity(plain_dump)
    validate_custom_dump(plain_dump)
    if custom_dump_identity(plain_dump) != dump_identity:
        message = f"PostgreSQL custom dump changed during validation: {plain_dump}"
        raise RuntimeRestoreError(message)
    selected_filestore = original_filestore or plain_filestore
    validate_filestore(selected_filestore)
    return RuntimeDataset("custom", plain_dump, selected_filestore, dump_identity)
