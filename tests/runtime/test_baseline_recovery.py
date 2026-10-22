"""Tests reusable database-reset baselines."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from godoo_cli.database.connection import DBConnection
from godoo_cli.runtime.baseline import baseline_database_name
from godoo_cli.runtime.locks import (
    runtime_readiness_marker,
    runtime_restore_marker,
)
from godoo_cli.runtime.reset import reset_runtime_from_template


def _connection() -> DBConnection:
    return DBConnection("host", 5432, "user", "password", "runtime", sslmode="require")


def _working_database() -> str:
    return f"godoo_reset_{hashlib.sha256(b'runtime').hexdigest()[:32]}"


def _rsync_copy(command: list[str]) -> int:
    source = Path(command[-2])
    target = Path(command[-1])
    for child in target.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True)
    return 0


def _prepare_baseline(data_dir: Path) -> tuple[str, Path]:
    template = baseline_database_name("runtime")
    source = data_dir / "filestore" / template
    source.mkdir(parents=True)
    (source / "attachment").write_text("baseline", encoding="utf-8")
    return template, source


def test_reset_reuses_baseline_and_deletes_extra_filestore_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uses an immutable baseline for repeated in-place resets."""
    data_dir = tmp_path / "data"
    template, source = _prepare_baseline(data_dir)
    target = data_dir / "filestore" / "runtime"
    target.mkdir(parents=True)
    (target / "obsolete").write_text("old", encoding="utf-8")
    created: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.reset.create_database",
        lambda connection, source_database, *, prefer_cow: created.append(
            (connection.db_name, source_database, prefer_cow)
        ),
    )
    monkeypatch.setattr("godoo_cli.runtime.reset.swap_database", lambda *_args: "previous")
    monkeypatch.setattr("godoo_cli.runtime.reset.cleanup_database", lambda *_args: None)

    for _ in range(2):
        assert (
            reset_runtime_from_template(
                connection=_connection(),
                data_dir=data_dir,
                template="",
                runner=_rsync_copy,
                database_exists_getter=lambda _connection, name: name == template,
            )
            == 0
        )
        assert (target / "attachment").read_text(encoding="utf-8") == "baseline"
        assert not (target / "obsolete").exists()
        (target / "obsolete").write_text("old", encoding="utf-8")

    assert created == [
        (_working_database(), template, True),
        (_working_database(), template, True),
    ]
    assert (source / "attachment").read_text(encoding="utf-8") == "baseline"
    lifecycle = json.loads(runtime_readiness_marker(data_dir, "runtime").read_text(encoding="utf-8"))
    assert lifecycle == {"database": "runtime", "outcome": "restored"}


def test_reset_failure_retains_marker_and_retries_same_working_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retains recovery evidence until a later reset attempt succeeds."""
    data_dir = tmp_path / "data"
    template, _source = _prepare_baseline(data_dir)
    created: list[str] = []
    monkeypatch.setattr(
        "godoo_cli.runtime.reset.create_database",
        lambda connection, _template, **_kwargs: created.append(connection.db_name),
    )
    monkeypatch.setattr("godoo_cli.runtime.reset.swap_database", lambda *_args: None)

    with pytest.raises(RuntimeError, match="pending restore marker was retained"):
        reset_runtime_from_template(
            connection=_connection(),
            data_dir=data_dir,
            template="",
            runner=lambda _command: 1,
            database_exists_getter=lambda _connection, name: name == template,
        )

    marker = runtime_restore_marker(data_dir, "runtime")
    staged_database = _working_database()
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "database": "runtime",
        "staged_database": staged_database,
    }
    assert (
        reset_runtime_from_template(
            connection=_connection(),
            data_dir=data_dir,
            template="",
            runner=_rsync_copy,
            database_exists_getter=lambda _connection, name: name == template,
        )
        == 0
    )
    assert created == [staged_database, staged_database]
    assert not marker.exists()


def test_reset_rejects_unrelated_pending_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not treat another restore operation as a reset retry."""
    data_dir = tmp_path / "data"
    template, _source = _prepare_baseline(data_dir)
    marker = runtime_restore_marker(data_dir, "runtime")
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"database": "runtime", "staged_database": "another-working-db"}), encoding="utf-8")
    created: list[object] = []
    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", lambda *_args, **_kwargs: created.append(1))

    with pytest.raises(RuntimeError, match="unrelated pending restore"):
        reset_runtime_from_template(
            connection=_connection(),
            data_dir=data_dir,
            template="",
            database_exists_getter=lambda _connection, name: name == template,
        )

    assert created == []


def test_missing_baseline_database_leaves_runtime_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails before reset work when the baseline database is absent."""
    data_dir = tmp_path / "data"
    _template, _source = _prepare_baseline(data_dir)
    target = data_dir / "filestore" / "runtime"
    target.mkdir()
    (target / "keep").write_text("current", encoding="utf-8")
    created: list[object] = []
    monkeypatch.setattr("godoo_cli.runtime.reset.create_database", lambda *_args, **_kwargs: created.append(1))

    with pytest.raises(RuntimeError, match=r"baseline database.*missing"):
        reset_runtime_from_template(
            connection=_connection(),
            data_dir=data_dir,
            template="",
            database_exists_getter=lambda _connection, _name: False,
        )

    assert (target / "keep").read_text(encoding="utf-8") == "current"
    assert created == []
