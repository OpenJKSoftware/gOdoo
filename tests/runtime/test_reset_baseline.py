"""Saved baselines remain independent and refresh as complete runtime pairs."""

import json
import shutil
from pathlib import Path

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime import baseline
from godoo_cli.runtime.locks import begin_runtime_restore, runtime_restore_marker


@pytest.fixture
def databases(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    state = {"restored": "original"}

    def copy_database(connection: DBConnection, template: str, *, prefer_cow: bool) -> None:
        assert prefer_cow
        state[connection.db_name] = state[template]

    def swap(connection: DBConnection, source: str) -> str | None:
        previous = None
        if connection.db_name in state:
            previous = "previous_baseline"
            state[previous] = state.pop(connection.db_name)
        state[connection.db_name] = state.pop(source)
        return previous

    monkeypatch.setattr(baseline, "create_database", copy_database)
    monkeypatch.setattr(baseline, "swap_database", swap)
    monkeypatch.setattr(baseline, "cleanup_database", lambda connection: state.pop(connection.db_name, None))
    monkeypatch.setattr(baseline, "copy_filestore", shutil.copytree)
    return state


def test_baseline_refresh_keeps_an_independent_database_and_filestore(
    tmp_path: Path, databases: dict[str, str]
) -> None:
    connection = DBConnection("db", 5432, "odoo", "secret", "runtime")
    source = tmp_path / "source"
    source.mkdir()
    (source / "attachment").write_text("original")
    baseline_name = baseline.baseline_database_name("runtime")

    baseline.save_runtime_baseline(
        connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
    )
    saved_filestore = tmp_path / "filestore" / baseline_name
    databases["restored"] = "changed"
    (source / "attachment").write_text("changed")
    assert databases[baseline_name] == "original"
    assert (saved_filestore / "attachment").read_text() == "original"

    (source / "attachment").unlink()
    (source / "replacement").write_text("new")
    baseline.save_runtime_baseline(
        connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
    )
    assert databases == {"restored": "changed", baseline_name: "changed"}
    assert not (saved_filestore / "attachment").exists()
    assert (saved_filestore / "replacement").read_text() == "new"
    assert not runtime_restore_marker(tmp_path, baseline_name).exists()


def test_failed_baseline_copy_preserves_the_previous_pair(
    tmp_path: Path, databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DBConnection("db", 5432, "odoo", "secret", "runtime")
    source = tmp_path / "source"
    source.mkdir()
    (source / "attachment").write_text("old")
    baseline_name = baseline.baseline_database_name("runtime")
    baseline.save_runtime_baseline(
        connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
    )

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        message = "database copy failed"
        raise OSError(message)

    monkeypatch.setattr(baseline, "create_database", fail_copy)
    (source / "attachment").write_text("new")
    with pytest.raises(OSError, match="database copy failed"):
        baseline.save_runtime_baseline(
            connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
        )

    assert databases[baseline_name] == "original"
    assert (tmp_path / "filestore" / baseline_name / "attachment").read_text() == "old"
    assert list((tmp_path / "filestore").iterdir()) == [tmp_path / "filestore" / baseline_name]


def test_fresh_baseline_replaces_an_interrupted_promotion(tmp_path: Path, databases: dict[str, str]) -> None:
    connection = DBConnection("db", 5432, "odoo", "secret", "runtime")
    source = tmp_path / "source"
    source.mkdir()
    (source / "attachment").write_text("fresh")
    baseline_name = baseline.baseline_database_name("runtime")
    marker = begin_runtime_restore(tmp_path, baseline_name, "interrupted_baseline")

    baseline.save_runtime_baseline(
        connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
    )

    assert databases[baseline_name] == "original"
    assert (tmp_path / "filestore" / baseline_name / "attachment").read_text() == "fresh"
    assert not marker.exists()


def test_failed_marker_replacement_preserves_pending_baseline(
    tmp_path: Path, databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DBConnection("db", 5432, "odoo", "secret", "runtime")
    source = tmp_path / "source"
    source.mkdir()
    baseline_name = baseline.baseline_database_name("runtime")
    marker = begin_runtime_restore(tmp_path, baseline_name, "interrupted_baseline")
    previous = marker.read_text()

    def fail_replace(*_args: object) -> None:
        message = "marker replacement failed"
        raise OSError(message)

    monkeypatch.setattr("godoo_cli.runtime.locks.os.replace", fail_replace)
    with pytest.raises(OSError, match="marker replacement failed"):
        baseline.save_runtime_baseline(
            connection=connection, source_database="restored", source_filestore=source, data_dir=tmp_path
        )

    assert marker.read_text() == previous
    assert not marker.with_suffix(".tmp").exists()
    assert databases == {"restored": "original"}


def test_ordinary_restore_cannot_replace_a_pending_marker(tmp_path: Path) -> None:
    marker = begin_runtime_restore(tmp_path, "runtime", "first_restore")

    with pytest.raises(FileExistsError):
        begin_runtime_restore(tmp_path, "runtime", "second_restore")

    assert json.loads(marker.read_text())["staged_database"] == "first_restore"
