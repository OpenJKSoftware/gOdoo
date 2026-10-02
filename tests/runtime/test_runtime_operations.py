"""Behavioral checks for runtime locking, diagnostics, and process shutdown."""

import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2
import pytest
import typer
from typer.testing import CliRunner

from godoo_cli.commands.runtime import status
from godoo_cli.database.state import DbBootstrapStatus
from godoo_cli.models import GodooConfig
from godoo_cli.runtime import lifecycle as runtime_lifecycle
from godoo_cli.runtime import status as runtime_status
from godoo_cli.runtime.lifecycle import LifecycleOutcome, deployment_init
from godoo_cli.runtime.locks import begin_runtime_restore, runtime_locks

LOGGER = logging.getLogger(__name__)


def _config(tmp_path: Path) -> GodooConfig:
    return GodooConfig(
        odoo_install_folder=tmp_path / "odoo",
        odoo_conf_path=tmp_path / "odoo.conf",
        workspace_addon_path=tmp_path / "addons",
        thirdparty_addon_path=tmp_path / "thirdparty",
        db_name="runtime",
        data_dir=tmp_path / "data",
    )


@pytest.mark.parametrize("db_status", list(DbBootstrapStatus))
@pytest.mark.parametrize("seed_requested", [False, True])
def test_initialization_state_seed_matrix(
    tmp_path: Path,
    db_status: DbBootstrapStatus,
    seed_requested: bool,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    """Guards the contract that initialization state seed matrix."""
    calls = []

    def initialize():
        return deployment_init(
            _config(tmp_path),
            seed_requested=seed_requested,
            seeder=lambda _config: calls.append("restore"),
            ensure=lambda _config: calls.append("bootstrap") or True,
            reconciler=lambda _config: calls.append("reconcile") or 0,
            status_getter=lambda _connection: db_status,
        )

    if db_status == DbBootstrapStatus.INVALID_DB:
        with pytest.raises(ValueError, match="invalid database"):
            initialize()
        assert calls == []
    else:
        outcome, result = initialize()
        assert result == 0
        if db_status == DbBootstrapStatus.BOOTSTRAPPED:
            assert outcome == LifecycleOutcome.READY
            assert calls == ["reconcile"]
        else:
            assert outcome == (LifecycleOutcome.RESTORED if seed_requested else LifecycleOutcome.BOOTSTRAPPED)
            assert calls == ["restore" if seed_requested else "bootstrap", "reconcile"]


def test_initialization_rejects_orphaned_filestore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    """Guards the contract that initialization rejects orphaned filestore."""
    config = _config(tmp_path)
    filestore = config.data_dir / "filestore" / config.db_name
    filestore.mkdir(parents=True)
    (filestore / "attachment").write_text("keep")
    with pytest.raises(ValueError, match="matching database and filestore"):
        deployment_init(
            config,
            seed_requested=True,
            seeder=lambda _config: pytest.fail("must not restore over orphaned data"),
            ensure=lambda _config: pytest.fail("must not bootstrap over orphaned data"),
            reconciler=lambda _config: pytest.fail("must not reconcile an inconsistent runtime"),
            status_getter=lambda _connection: DbBootstrapStatus.NO_DB,
        )
    assert (filestore / "attachment").read_text() == "keep"


def test_unfinished_restore_marker_blocks_ready_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    """Guards the contract that unfinished restore marker blocks ready runtime."""
    config = _config(tmp_path)
    marker = begin_runtime_restore(config.data_dir, config.db_name, "staged")
    with pytest.raises(ValueError, match="unfinished restore"):
        deployment_init(
            config,
            seed_requested=False,
            seeder=None,
            ensure=lambda _config: pytest.fail("must not bootstrap during pending recovery"),
            reconciler=lambda _config: pytest.fail("must not reconcile during pending recovery"),
            status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED,
        )
    monkeypatch.setattr(runtime_status, "classify_bootstrap_state", lambda _: DbBootstrapStatus.BOOTSTRAPPED)
    payload, exit_code = status.inspect_runtime(config.db_connection, config.data_dir, tmp_path / "provenance")
    assert exit_code == 22
    assert payload["state"] == "inconsistent"
    assert marker.is_file()


def test_competing_initializations_bootstrap_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(runtime_lifecycle, "require_runtime_database_major", lambda _config: 19)
    """Guards the contract that competing initializations bootstrap only once."""
    config = _config(tmp_path)
    ready = False
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def bootstrap(_config: GodooConfig) -> bool:
        nonlocal ready
        with runtime_locks(config.data_dir, config.db_name):
            calls.append("bootstrap")
            entered.set()
            assert release.wait(5)
            ready = True
        return True

    def initialize():
        return deployment_init(
            config,
            seed_requested=False,
            seeder=None,
            ensure=bootstrap,
            reconciler=lambda _config: calls.append("reconcile") or 0,
            status_getter=lambda _connection: DbBootstrapStatus.BOOTSTRAPPED if ready else DbBootstrapStatus.NO_DB,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(initialize)
        assert entered.wait(5)
        second = pool.submit(initialize)
        release.set()
        assert first.result(timeout=5) == (LifecycleOutcome.BOOTSTRAPPED, 0)
        assert second.result(timeout=5) == (LifecycleOutcome.READY, 0)
    assert calls == ["bootstrap", "reconcile", "reconcile"]


def test_runtime_locks_coordinate_separate_processes_and_release_on_error(tmp_path: Path):
    """Guards the contract that runtime locks coordinate separate processes and release on error."""
    child_code = (
        "import sys; from pathlib import Path; from godoo_cli.runtime.locks import runtime_locks\n"
        "print('waiting', flush=True)\n"
        "with runtime_locks(Path(sys.argv[1]), 'target', 'source'):\n"
        "    print('acquired', flush=True)\n"
    )
    with runtime_locks(tmp_path, "source", "target"):
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        assert child.stderr is not None
        try:
            ready = select.select([child.stdout, child.stderr], [], [], 15)[0]
            if child.stdout not in ready:
                child.kill()
                output, error = child.communicate(timeout=5)
                pytest.fail(f"lock child did not reach handshake: stdout={output!r}, stderr={error!r}")
            assert child.stdout.readline().strip() == "waiting"
            assert not select.select([child.stdout], [], [], 0.5)[0]
        except BaseException:
            child.kill()
            child.wait()
            raise
    try:
        output, _ = child.communicate(timeout=5)
        assert child.returncode == 0
        assert output.strip() == "acquired"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    with pytest.raises(RuntimeError), runtime_locks(tmp_path, "source"):
        raise RuntimeError
    with runtime_locks(tmp_path, "source"):
        assert len(list(tmp_path.glob(".godoo/locks/*.lock"))) == 2


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_odoo_process_receives_stop_signal_and_wrapper_waits(tmp_path: Path, signum: int):
    """Guards the contract that odoo process receives stop signal and wrapper waits."""
    marker = tmp_path / "stopped"
    child_code = (
        "import pathlib, signal, sys\n"
        "def stop(signum, frame):\n"
        "    pathlib.Path(sys.argv[1]).write_text(str(signum))\n"
        "    sys.exit(9)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "print('ready', flush=True)\n"
        "signal.pause()\n"
    )
    wrapper_code = (
        "import sys; from godoo_cli.runtime.odoo import run_odoo_command; "
        "sys.exit(run_odoo_command([sys.executable, '-c', sys.argv[1], sys.argv[2]]).returncode)"
    )
    wrapper = subprocess.Popen(
        [sys.executable, "-c", wrapper_code, child_code, str(marker)], stdout=subprocess.PIPE, text=True
    )
    assert wrapper.stdout is not None
    try:
        assert select.select([wrapper.stdout], [], [], 5)[0]
        assert wrapper.stdout.readline().strip() == "ready"
        os.kill(wrapper.pid, signum)
        assert wrapper.wait(timeout=5) == 9
        assert marker.read_text() == str(signum)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait()


def test_runtime_status_json_uses_production_metadata_without_host_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Guards the contract that runtime status json uses production metadata without host sources."""
    metadata = tmp_path / "provenance.json"
    metadata.write_text(json.dumps({"sources": [{"resolved_commit": "abc", "worktree": "/missing/host/path"}]}))
    monkeypatch.setattr(runtime_status, "classify_bootstrap_state", lambda _: DbBootstrapStatus.BOOTSTRAPPED)
    app = typer.Typer()
    app.command()(status.runtime_status)
    result = CliRunner().invoke(
        app,
        [
            "--db-name",
            "runtime",
            "--db-user",
            "odoo",
            "--json",
            "--data-dir",
            str(tmp_path / "uncreated"),
            "--provenance-path",
            str(metadata),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state"] == "ready"
    assert payload["release"]["sources"] == [{"resolved_commit": "abc"}]
    assert payload["release"]["kind"] == "production"
    assert not (tmp_path / "uncreated").exists()


def test_runtime_status_reports_connection_failure_without_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Guards the contract that runtime status reports connection failure without credentials."""

    def unavailable(_connection: object):
        message = "password=do-not-print"
        raise psycopg2.OperationalError(message)

    monkeypatch.setattr(runtime_status, "classify_bootstrap_state", unavailable)
    payload, exit_code = status.inspect_runtime(_config(tmp_path).db_connection, tmp_path, tmp_path / "provenance")
    assert exit_code == 1
    assert payload["state"] == "unavailable"
    assert "do-not-print" not in json.dumps(payload)


@pytest.mark.parametrize("verbose", [False, True])
def test_operational_logs_leave_json_stdout_parseable(verbose: bool):
    """Guards the contract that operational logs leave json stdout parseable."""
    code = (
        "import json, logging; from godoo_cli.helpers.system import set_logging; "
        f"set_logging(verbose={verbose!r}); "
        "logging.getLogger('godoo.test').error('database unavailable'); "
        "print(json.dumps({'state': 'unavailable'}))"
    )
    result = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"state": "unavailable"}
    assert "database unavailable" in result.stderr
