"""Focused tests for clone-preflight filestore copying."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from godoo_cli.runtime.filestore import _clone_probe_candidate, copy_filestore


def test_clone_probe_descends_into_a_hash_bucket_before_scanning_all_siblings(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "empty").touch()
    for bucket_number in range(256):
        bucket = source / f"{bucket_number:02x}"
        bucket.mkdir()
        (bucket / "blob").write_bytes(b"attachment")

    candidate = _clone_probe_candidate(source)

    assert candidate is not None
    assert candidate.parent.parent == source
    assert candidate.read_bytes() == b"attachment"


def test_linux_clone_preflight_uses_a_single_file_then_materializes_independent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept").write_text("original")
    (source / "alias").symlink_to(source / "kept")
    destination = tmp_path / "stage"
    commands: list[list[str]] = []

    def copy_with_cp(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "-Lp" in command:
            shutil.copy2(command[-2], command[-1], follow_symlinks=True)
        else:
            shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=True)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("godoo_cli.runtime.filestore.shutil.which", lambda _command: "/usr/bin/cp")
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", copy_with_cp)
    caplog.set_level(logging.INFO, logger="godoo_cli.runtime.filestore")

    copy_filestore(source, destination, platform_name="linux")

    assert commands[0][:3] == ["cp", "-Lp", "--reflink=always"]
    assert Path(commands[0][-2]) in {source / "kept", source / "alias"}
    assert commands[0][-1] == str(destination / ".godoo-filestore-clone-probe")
    assert commands[1] == ["cp", "-RLp", "--reflink=always", f"{source}/.", str(destination)]
    assert (destination / "kept").read_text() == "original"
    assert not (destination / "alias").is_symlink()
    (destination / "kept").write_text("changed")
    assert (source / "kept").read_text() == "original"
    assert "Filestore clone preflight succeeded; copying with reflink" in caplog.messages
    assert "Copied filestore with linux reflink" in caplog.messages


def test_linux_unsupported_clone_preflight_skips_recursive_cp_and_uses_rsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept").write_text("original")
    destination = tmp_path / "stage"
    commands: list[list[str]] = []
    rsync_calls: list[tuple[Path, Path]] = []

    def unsupported_clone(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 1)

    def copy_with_rsync(copy_source: Path, copy_destination: Path) -> tuple[bool, str | None]:
        rsync_calls.append((copy_source, copy_destination))
        shutil.copytree(copy_source, copy_destination, symlinks=False, dirs_exist_ok=True)
        return True, None

    monkeypatch.setattr("godoo_cli.runtime.filestore.shutil.which", lambda _command: "/usr/bin/cp")
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", unsupported_clone)
    monkeypatch.setattr("godoo_cli.runtime.filestore._copy_with_rsync", copy_with_rsync)
    caplog.set_level(logging.INFO, logger="godoo_cli.runtime.filestore")

    copy_filestore(source, destination, platform_name="linux")

    assert commands == [
        ["cp", "-Lp", "--reflink=always", str(source / "kept"), str(destination / ".godoo-filestore-clone-probe")]
    ]
    assert rsync_calls == [(source, destination)]
    assert (destination / "kept").read_text() == "original"
    assert any("Filestore cloning is unavailable" in message for message in caplog.messages)
    assert "Copying filestore with rsync" in caplog.messages
    assert "Copied filestore with rsync" in caplog.messages


def test_linux_clone_copy_failure_cleans_partial_stage_before_rsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept").write_text("original")
    destination = tmp_path / "stage"

    def copy_with_failed_recursive_clone(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-Lp" in command:
            shutil.copy2(command[-2], command[-1], follow_symlinks=True)
            return subprocess.CompletedProcess(command, 0)
        (destination / "partial").write_text("partial")
        return subprocess.CompletedProcess(command, 1)

    def copy_with_rsync(copy_source: Path, copy_destination: Path) -> tuple[bool, str | None]:
        shutil.copytree(copy_source, copy_destination, symlinks=False, dirs_exist_ok=True)
        return True, None

    monkeypatch.setattr("godoo_cli.runtime.filestore.shutil.which", lambda _command: "/usr/bin/cp")
    monkeypatch.setattr("godoo_cli.runtime.filestore.subprocess.run", copy_with_failed_recursive_clone)
    monkeypatch.setattr("godoo_cli.runtime.filestore._copy_with_rsync", copy_with_rsync)
    caplog.set_level(logging.INFO, logger="godoo_cli.runtime.filestore")

    copy_filestore(source, destination, platform_name="linux")

    assert (destination / "kept").read_text() == "original"
    assert not (destination / "partial").exists()
    assert any("removing partial stage" in message for message in caplog.messages)
    assert "Removed partial filestore stage; using rsync" in caplog.messages
