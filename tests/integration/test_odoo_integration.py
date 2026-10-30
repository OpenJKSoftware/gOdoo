"""Opt-in integration tests for a live gOdoo container."""

import logging
import os
import shutil
import subprocess
import uuid
import zipfile
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql

from godoo_cli.models import GodooConfig
from godoo_cli.runtime.archive import dump_runtime_archive, load_runtime_archive
from godoo_cli.runtime.baseline import baseline_database_name, save_runtime_baseline
from godoo_cli.runtime.hooks import discover_hooks, run_hook_phase
from godoo_cli.runtime.lifecycle import (
    LifecycleOutcome,
    deployment_init,
    ensure_runtime,
    reconcile_runtime,
)
from godoo_cli.runtime.locks import read_runtime_lifecycle, runtime_readiness_marker
from godoo_cli.runtime.promotion import drop_database_strict
from godoo_cli.runtime.reset import reset_empty_runtime, reset_runtime_from_template
from godoo_cli.runtime.restore import RuntimeRestoreError, restore_custom_runtime, runtime_filestore_path

LOGGER = logging.getLogger(__name__)


pytestmark = [
    pytest.mark.odoo_integration,
    pytest.mark.skipif(
        os.environ.get("GODOO_RUN_ODOO_INTEGRATION") != "1",
        reason="set GODOO_RUN_ODOO_INTEGRATION=1 to run real Odoo/PostgreSQL tests",
    ),
]

DATABASE_PREFIX = "godoo_it_"


@dataclass
class LiveStack:
    """Isolated database and filestore manager for the running container."""

    root: Path
    python_path: Path
    odoo_path: Path
    workspace_addons: Path
    thirdparty_addons: Path
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    database_names: list[str] = field(default_factory=list)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    def new_database_name(self, label: str) -> str:
        name = f"{DATABASE_PREFIX}{label}_{uuid.uuid4().hex[:10]}"
        self.database_names.append(name)
        return name

    def config(self, db_name: str, *, extra_addon_paths: list[Path] | None = None) -> GodooConfig:
        config_path = self.root / f"{db_name}.conf"
        addon_paths = [self.odoo_path / "odoo" / "addons", self.odoo_path / "addons", *(extra_addon_paths or [])]
        config_lines = [
            "[options]",
            f"addons_path = {','.join(str(path) for path in addon_paths if path.is_dir())}",
            f"data_dir = {self.data_dir}",
            f"db_host = {self.db_host}",
            f"db_name = {db_name}",
            f"db_password = {self.db_password}",
            f"db_user = {self.db_user}",
            f"dbfilter = ^{db_name}$",
            "list_db = True",
            "workers = 0",
            "with_demo = False",
        ]
        if self.db_port:
            config_lines.append(f"db_port = {self.db_port}")
        config_path.write_text("\n".join(config_lines) + "\n")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return GodooConfig(
            odoo_install_folder=self.odoo_path,
            odoo_conf_path=config_path,
            workspace_addon_path=self.workspace_addons,
            thirdparty_addon_path=self.thirdparty_addons,
            data_dir=self.data_dir,
            multithread_worker_count=0,
            languages="en_US",
            db_user=self.db_user,
            db_password=self.db_password,
            db_host=self.db_host,
            db_port=self.db_port,
            db_name=db_name,
            db_filter=db_name,
        )

    def initialize(self, config: GodooConfig, **_kwargs: object) -> int:
        command = [
            str(self.python_path),
            str(config.odoo_bin_path),
            "--config",
            str(config.odoo_conf_path),
            "--data-dir",
            str(config.data_dir),
            "-d",
            config.db_name,
            "--init",
            "base",
            "--without-demo",
            "--stop-after-init",
        ]
        environment = os.environ.copy()
        environment["DEBUG"] = "0"
        return subprocess.run(command, check=False, env=environment).returncode

    def connect(self, db_name: str = "postgres"):
        return psycopg2.connect(
            host=self.db_host,
            port=self.db_port or None,
            user=self.db_user,
            password=self.db_password,
            dbname=db_name,
            connect_timeout=5,
        )

    def execute(self, db_name: str, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        with self.connect(db_name) as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)

    def scalar(self, db_name: str, statement: str, parameters: tuple[object, ...] | None = None) -> object:
        with self.connect(db_name) as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            row = cursor.fetchone()
        assert row is not None
        return row[0]

    def database_exists(self, db_name: str) -> bool:
        return bool(
            self.scalar(
                "postgres",
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
                (db_name,),
            )
        )

    def dump_custom(self, db_name: str, destination: Path) -> int:
        command = ["pg_dump", "--format=custom", "--file", str(destination)]
        if self.db_host:
            command.extend(["--host", self.db_host])
        if self.db_port:
            command.extend(["--port", str(self.db_port)])
        if self.db_user:
            command.extend(["--username", self.db_user])
        command.append(db_name)
        environment = os.environ.copy()
        if self.db_password:
            environment["PGPASSWORD"] = self.db_password
        return subprocess.run(command, check=False, env=environment).returncode

    def cleanup(self) -> None:
        for db_name in self.database_names:
            if not db_name.startswith(DATABASE_PREFIX):
                message = f"Refusing to clean up unsafe integration database name: {db_name}"
                raise AssertionError(message)
            connection = self.connect()
            try:
                connection.autocommit = True
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (db_name,)
                    )
                    cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
            finally:
                connection.close()
            filestore = runtime_filestore_path(self.data_dir, db_name)
            if filestore.exists():
                shutil.rmtree(filestore)


@pytest.fixture
def live_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[LiveStack, None, None]:
    monkeypatch.setenv("DEBUG", "0")
    execution_env = os.environ.get("VIRTUAL_ENV")
    odoo_python = Path(execution_env, "bin", "python") if execution_env else Path.cwd() / ".venv" / "bin" / "python"
    monkeypatch.setenv("PATH", f"{odoo_python.parent}:{os.environ['PATH']}")
    odoo_path = Path(os.environ.get("ODOO_MAIN_FOLDER", "/odoo/odoo"))
    stack = LiveStack(
        root=tmp_path,
        python_path=odoo_python,
        odoo_path=odoo_path,
        workspace_addons=Path(os.environ.get("ODOO_WORKSPACE_ADDON_LOCATION", "/odoo/godoo_workspace/addons")),
        thirdparty_addons=odoo_path.parent / "thirdparty",
        db_host=os.environ.get("ODOO_DB_HOST", "/var/run/postgresql"),
        db_port=int(os.environ.get("ODOO_DB_PORT", "0")),
        db_user=os.environ.get("ODOO_DB_USER", "odoo_user"),
        db_password=os.environ.get("ODOO_DB_PASSWORD", "odoo"),
    )
    if not stack.odoo_path.joinpath("odoo-bin").is_file():
        pytest.skip(f"Odoo binary is unavailable at {stack.odoo_path / 'odoo-bin'}")
    if not stack.python_path.is_file():
        pytest.skip(f"Odoo Python is unavailable at {stack.python_path}")
    try:
        connection = stack.connect()
        connection.close()
    except psycopg2.OperationalError as error:
        pytest.skip(f"PostgreSQL is unavailable: {error}")
    try:
        yield stack
    finally:
        stack.cleanup()


def test_real_lifecycle_bootstraps_and_runs_ordered_hooks(live_stack: LiveStack, tmp_path: Path) -> None:
    """Proves hooks share a registry, commit separately, and skip satisfied state on repeat init."""
    db_name = live_stack.new_database_name("lifecycle")
    config = live_stack.config(db_name)
    after_reconcile_hooks = tmp_path / "after-reconcile"
    after_reconcile_hooks.mkdir()
    (after_reconcile_hooks / "20_second.py").write_text(
        "import os\n"
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.second', phase='after_reconcile')\n"
        "def needs_run(ctx):\n"
        "    return not bool(ctx.env['ir.config_parameter'].sudo().get_param('godoo.integration.second'))\n"
        "def run(ctx):\n"
        "    params = ctx.env['ir.config_parameter'].sudo()\n"
        "    first = params.get_param('godoo.integration.first')\n"
        "    assert first, 'first hook must commit before second hook starts'\n"
        "    ctx.env.cr.execute('SELECT txid_current()')\n"
        "    txid = ctx.env.cr.fetchone()[0]\n"
        "    value = f'{os.getpid()}/{id(ctx.env.registry)}/{txid}/{ctx.outcome}'\n"
        "    params.set_param('godoo.integration.second', value)\n"
    )
    (after_reconcile_hooks / "10_first.py").write_text(
        "import os\n"
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.first', phase='after_reconcile')\n"
        "def needs_run(ctx):\n"
        "    return not bool(ctx.env['ir.config_parameter'].sudo().get_param('godoo.integration.first'))\n"
        "def run(ctx):\n"
        "    ctx.env.cr.execute('SELECT txid_current()')\n"
        "    txid = ctx.env.cr.fetchone()[0]\n"
        "    value = f'{os.getpid()}/{id(ctx.env.registry)}/{txid}/{ctx.outcome}'\n"
        "    ctx.env['ir.config_parameter'].sudo().set_param('godoo.integration.first', value)\n"
    )

    def ensure(conf: GodooConfig) -> bool:
        return ensure_runtime(
            conf,
            preparer=lambda _config: None,
            bootstrapper=live_stack.initialize,
            install_workspace_modules=False,
            allow_lifecycle_retry=True,
        )

    def reconcile(conf: GodooConfig) -> int:
        return reconcile_runtime(conf, preparer=lambda _config: None, allow_lifecycle_retry=True)

    result = deployment_init(
        config,
        seeder=None,
        ensure=ensure,
        reconciler=reconcile,
        hooks=discover_hooks([after_reconcile_hooks]),
    )

    assert result == (LifecycleOutcome.BOOTSTRAPPED, 0)
    values = [
        str(live_stack.scalar(db_name, "SELECT value FROM ir_config_parameter WHERE key = %s", (key,)))
        for key in ("godoo.integration.first", "godoo.integration.second")
    ]
    first_pid, first_registry, first_txid, first_outcome = values[0].split("/")
    second_pid, second_registry, second_txid, second_outcome = values[1].split("/")
    assert first_pid == second_pid
    assert first_registry == second_registry
    assert first_txid != second_txid
    assert first_outcome == second_outcome == "bootstrapped"
    previous_write_date = live_stack.scalar(
        db_name, "SELECT max(write_date)::text FROM ir_config_parameter WHERE key LIKE 'godoo.integration.%'"
    )

    result = deployment_init(
        config,
        seeder=None,
        ensure=ensure,
        reconciler=reconcile,
        hooks=discover_hooks([after_reconcile_hooks]),
    )

    assert result == (LifecycleOutcome.READY, 0)
    assert (
        live_stack.scalar(
            db_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.first'",
        )
        == values[0]
    )
    assert (
        live_stack.scalar(
            db_name, "SELECT max(write_date)::text FROM ir_config_parameter WHERE key LIKE 'godoo.integration.%'"
        )
        == previous_write_date
    )


def test_real_hook_retry_preserves_committed_work_and_original_outcome(live_stack: LiveStack, tmp_path: Path) -> None:
    """Proves a partial internal commit survives failure and its state guard makes retry safe."""
    db_name = live_stack.new_database_name("hook_retry")
    config = live_stack.config(db_name)
    hook_dir = tmp_path / "retry-hooks"
    hook_dir.mkdir()
    (hook_dir / "10_partial.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.partial', phase='after_reconcile', on=('bootstrapped',))\n"
        "def needs_run(ctx):\n"
        "    return not bool(ctx.env['ir.config_parameter'].sudo().get_param('godoo.integration.partial'))\n"
        "def run(ctx):\n"
        "    ctx.env['ir.config_parameter'].sudo().set_param('godoo.integration.partial', 'committed')\n"
        "    ctx.env.cr.commit()\n"
        "    raise RuntimeError('integration failure after internal commit')\n"
    )
    (hook_dir / "20_finish.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.finish', phase='after_reconcile', on=('bootstrapped',))\n"
        "def run(ctx):\n"
        "    assert ctx.outcome == 'bootstrapped'\n"
        "    ctx.env['ir.config_parameter'].sudo().set_param('godoo.integration.finish', 'done')\n"
    )

    def ensure(conf: GodooConfig) -> bool:
        return ensure_runtime(
            conf,
            preparer=lambda _config: None,
            bootstrapper=live_stack.initialize,
            install_workspace_modules=False,
            allow_lifecycle_retry=True,
        )

    arguments = {
        "ensure": ensure,
        "reconciler": lambda _config: 0,
        "hooks": discover_hooks([hook_dir]),
    }
    outcome, result = deployment_init(config, **arguments)
    assert outcome == LifecycleOutcome.BOOTSTRAPPED
    assert result != 0
    marker = runtime_readiness_marker(config.data_dir, db_name)
    assert read_runtime_lifecycle(marker, db_name) == {"database": db_name, "outcome": "bootstrapped"}
    assert (
        live_stack.scalar(db_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.partial'")
        == "committed"
    )
    assert deployment_init(config, **arguments) == (LifecycleOutcome.READY, 0)
    assert not marker.exists()
    assert (
        live_stack.scalar(db_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.finish'")
        == "done"
    )


def test_real_lifecycle_seeds_database_and_filestore_from_odoo_archive(live_stack: LiveStack) -> None:
    """Proves archive seeding restores both live database data and its paired filestore before reconciliation."""
    source_name = live_stack.new_database_name("seed_source")
    target_name = live_stack.new_database_name("seed_target")
    source_config = live_stack.config(source_name)
    target_config = live_stack.config(target_name)
    archive = live_stack.root / "runtime-seed.zip"
    assert live_stack.initialize(source_config) == 0

    live_stack.execute(
        source_name,
        "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date) "
        "VALUES ('godoo.integration.seed', 'from-archive', 1, NOW(), 1, NOW())",
    )
    source_filestore = runtime_filestore_path(live_stack.data_dir, source_name)
    source_filestore.mkdir(parents=True, exist_ok=True)
    (source_filestore / "seed-marker").write_text("seed filestore")
    assert (
        dump_runtime_archive(
            db_name=source_name,
            archive_path=archive,
            odoo_bin_path=live_stack.odoo_path / "odoo-bin",
            odoo_conf_path=source_config.odoo_conf_path,
            data_dir=live_stack.data_dir,
        )
        == 0
    )

    def seed(conf: GodooConfig) -> None:
        result = load_runtime_archive(
            connection=conf.db_connection,
            data_dir=conf.data_dir,
            archive_path=archive,
            odoo_bin_path=live_stack.odoo_path / "odoo-bin",
            odoo_conf_path=conf.odoo_conf_path,
            force=True,
        )
        assert result == 0

    result = deployment_init(
        target_config,
        seeder=seed,
        ensure=lambda _config: pytest.fail("seeded runtime must not bootstrap"),
        reconciler=lambda config: reconcile_runtime(config, preparer=lambda _config: None, allow_lifecycle_retry=True),
        seed_requested=True,
    )

    assert result == (LifecycleOutcome.RESTORED, 0)
    assert (
        live_stack.scalar(
            target_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.seed'",
        )
        == "from-archive"
    )
    target_filestore = runtime_filestore_path(live_stack.data_dir, target_name)
    assert (target_filestore / "seed-marker").read_text() == "seed filestore"


def test_real_legacy_restore_promotes_complete_database_and_filestore(live_stack: LiveStack) -> None:
    """Proves a legacy restore replaces the live database and filestore together, without retaining target files."""
    source_name = live_stack.new_database_name("legacy_source")
    target_name = live_stack.new_database_name("legacy_target")
    source_config = live_stack.config(source_name)
    target_config = live_stack.config(target_name)
    dump = live_stack.root / "odoo.dump"
    assert live_stack.initialize(source_config) == 0
    assert live_stack.initialize(target_config) == 0

    live_stack.execute(
        source_name,
        "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date) "
        "VALUES ('godoo.integration.legacy', 'restored', 1, NOW(), 1, NOW())",
    )
    source_filestore = runtime_filestore_path(live_stack.data_dir, source_name)
    target_filestore = runtime_filestore_path(live_stack.data_dir, target_name)
    source_filestore.mkdir(parents=True, exist_ok=True)
    target_filestore.mkdir(parents=True, exist_ok=True)
    (source_filestore / "restored-marker").write_text("restored filestore")
    (target_filestore / "obsolete-marker").write_text("obsolete filestore")
    assert live_stack.dump_custom(source_name, dump) == 0

    with pytest.raises(RuntimeRestoreError, match="already exists"):
        restore_custom_runtime(
            connection=target_config.db_connection,
            data_dir=live_stack.data_dir,
            db_template="template0",
            dump_path=dump,
            filestore_source=source_filestore,
        )

    restore_custom_runtime(
        connection=target_config.db_connection,
        data_dir=live_stack.data_dir,
        db_template="template0",
        dump_path=dump,
        filestore_source=source_filestore,
        force=True,
    )

    assert (
        live_stack.scalar(
            target_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.legacy'",
        )
        == "restored"
    )
    assert (target_filestore / "restored-marker").read_text() == "restored filestore"
    assert not (target_filestore / "obsolete-marker").exists()


def test_native_archive_sql_failure_preserves_ready_runtime(live_stack: LiveStack) -> None:
    """Proves an invalid native archive leaves the ready database, filestore, and temporary restore state intact."""
    target_name = live_stack.new_database_name("native_failure")
    config = live_stack.config(target_name)
    assert live_stack.initialize(config) == 0
    filestore = runtime_filestore_path(live_stack.data_dir, target_name)
    filestore.mkdir(parents=True, exist_ok=True)
    (filestore / "preserved").write_text("original")
    archive = live_stack.root / "original.zip"
    assert (
        dump_runtime_archive(
            db_name=target_name,
            archive_path=archive,
            odoo_bin_path=config.odoo_bin_path,
            odoo_conf_path=config.odoo_conf_path,
            data_dir=config.data_dir,
        )
        == 0
    )
    invalid_archive = live_stack.root / "invalid-sql.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(invalid_archive, "w") as invalid:
        for member in original.infolist():
            content = original.read(member.filename)
            if member.filename == "dump.sql":
                content += b"\nSELECT 1 / 0;\n"
            invalid.writestr(member, content)
    result = load_runtime_archive(
        connection=config.db_connection,
        data_dir=config.data_dir,
        archive_path=invalid_archive,
        odoo_bin_path=config.odoo_bin_path,
        odoo_conf_path=config.odoo_conf_path,
        force=True,
    )
    assert result != 0
    assert live_stack.scalar(target_name, "SELECT state FROM ir_module_module WHERE name = 'base'") == "installed"
    assert (filestore / "preserved").read_text() == "original"
    assert (
        live_stack.scalar("postgres", "SELECT COUNT(*) FROM pg_database WHERE datname LIKE 'godoo_native_restore_%'")
        == 0
    )


def test_real_reset_replaces_and_drops_database_and_filestore(live_stack: LiveStack) -> None:
    """Proves a live reset promotes template data and filestore, then drops both target resources on request."""
    template_name = live_stack.new_database_name("template")
    target_name = live_stack.new_database_name("target")
    template_config = live_stack.config(template_name)
    target_config = live_stack.config(target_name)
    assert live_stack.initialize(template_config) == 0
    assert live_stack.initialize(target_config) == 0

    live_stack.execute(
        template_name,
        "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date) "
        "VALUES ('godoo.integration.reset', 'template', 1, NOW(), 1, NOW())",
    )
    live_stack.execute(
        target_name,
        "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date) "
        "VALUES ('godoo.integration.reset', 'target', 1, NOW(), 1, NOW())",
    )
    template_filestore = runtime_filestore_path(live_stack.data_dir, template_name)
    target_filestore = runtime_filestore_path(live_stack.data_dir, target_name)
    template_filestore.mkdir(parents=True, exist_ok=True)
    target_filestore.mkdir(parents=True, exist_ok=True)
    (template_filestore / "from-template").write_text("template filestore")
    (target_filestore / "obsolete-target").write_text("old filestore")

    result = reset_runtime_from_template(
        connection=target_config.db_connection,
        data_dir=live_stack.data_dir,
        odoo_conf_path=target_config.odoo_conf_path,
        template=template_name,
    )

    assert result == 0
    assert (
        live_stack.scalar(
            target_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.reset'",
        )
        == "template"
    )
    assert (target_filestore / "from-template").read_text() == "template filestore"
    assert not (target_filestore / "obsolete-target").exists()

    result = reset_empty_runtime(
        connection=target_config.db_connection,
        data_dir=live_stack.data_dir,
        odoo_conf_path=target_config.odoo_conf_path,
    )

    assert result == 0
    assert not live_stack.database_exists(target_name)
    assert not target_filestore.exists()


def test_real_saved_baseline_supports_repeated_resets(live_stack: LiveStack) -> None:
    """Restore database values and files repeatedly without changing the saved baseline."""
    source_name = live_stack.new_database_name("baseline_source")
    target_name = live_stack.new_database_name("baseline_target")
    source_config = live_stack.config(source_name)
    target_config = live_stack.config(target_name)
    baseline_name = baseline_database_name(target_name)
    baseline_connection = target_config.db_connection.with_db(baseline_name)
    baseline_filestore = runtime_filestore_path(live_stack.data_dir, baseline_name)
    source_filestore = runtime_filestore_path(live_stack.data_dir, source_name)
    assert live_stack.initialize(source_config) == 0
    live_stack.execute(
        source_name,
        "INSERT INTO ir_config_parameter (key, value) VALUES ('godoo.integration.baseline', 'saved')",
    )
    source_filestore.mkdir(parents=True, exist_ok=True)
    (source_filestore / "saved").write_text("baseline file")

    try:
        save_runtime_baseline(
            connection=target_config.db_connection,
            source_database=source_name,
            source_filestore=source_filestore,
            data_dir=live_stack.data_dir,
        )
        for _attempt in range(2):
            assert (
                reset_runtime_from_template(connection=target_config.db_connection, data_dir=live_stack.data_dir) == 0
            )
            assert (
                live_stack.scalar(
                    target_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.baseline'"
                )
                == "saved"
            )
            target_filestore = runtime_filestore_path(live_stack.data_dir, target_name)
            assert (target_filestore / "saved").read_text() == "baseline file"
            assert not (target_filestore / "extra").exists()
            live_stack.execute(
                target_name, "UPDATE ir_config_parameter SET value = 'changed' WHERE key = 'godoo.integration.baseline'"
            )
            (target_filestore / "extra").write_text("discard on reset")
        assert (
            live_stack.scalar(
                baseline_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.baseline'"
            )
            == "saved"
        )
        assert not (baseline_filestore / "extra").exists()
    finally:
        drop_database_strict(baseline_connection)
        if baseline_filestore.exists():
            shutil.rmtree(baseline_filestore)


def test_real_once_hook_receipt_commits_with_action_and_survives_retry(live_stack: LiveStack, tmp_path: Path) -> None:
    """Earlier transactional repairs remain complete after a later hook failure."""
    db_name = live_stack.new_database_name("once_hooks")
    config = live_stack.config(db_name)
    assert live_stack.initialize(config) == 0
    directory = tmp_path / "once-hooks"
    directory.mkdir()
    (directory / "10_repair.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.repair', phase='after_reconcile', once=True)\n"
        "def run(ctx):\n"
        "    params = ctx.env['ir.config_parameter'].sudo()\n"
        "    params.set_param('godoo.it.count', int(params.get_param('godoo.it.count', '0')) + 1)\n"
    )
    (directory / "20_later.py").write_text(
        "from godoo_cli.runtime.hooks import Hook\n"
        "HOOK = Hook(id='integration.later', phase='after_reconcile', once=True)\n"
        "def run(ctx):\n"
        "    params = ctx.env['ir.config_parameter'].sudo()\n"
        "    params.set_param('godoo.it.partial', 'must roll back on failure')\n"
        "    if not params.get_param('godoo.it.allow'): raise RuntimeError('later repair failed')\n"
    )
    selected = discover_hooks([directory])

    def run() -> int:
        return run_hook_phase(config, selected, phase="after_reconcile", outcome="ready", environment="review")

    assert run() != 0
    assert live_stack.scalar(db_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.it.count'") == "1"
    assert live_stack.scalar(db_name, "SELECT count(*) FROM ir_config_parameter WHERE key = 'godoo.it.partial'") == 0
    live_stack.execute(db_name, "INSERT INTO ir_config_parameter (key, value) VALUES ('godoo.it.allow', '1')")
    assert run() == 0
    assert run() == 0
    assert live_stack.scalar(db_name, "SELECT value FROM ir_config_parameter WHERE key = 'godoo.it.count'") == "1"
