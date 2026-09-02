"""Opt-in integration tests for a live gOdoo container."""

import os
import shutil
import subprocess
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psycopg2
import pytest
from psycopg2 import sql

from godoo_cli.commands import lifecycle as lifecycle_commands
from godoo_cli.commands.db.archive import dump_runtime_archive, load_runtime_archive
from godoo_cli.commands.db.reset import reset_empty_runtime, reset_runtime_from_template
from godoo_cli.commands.db.restore import restore_custom_runtime, runtime_filestore_path
from godoo_cli.lifecycle import LifecycleOutcome, deployment_init, ensure_runtime, reconcile_runtime
from godoo_cli.models import GodooConfig

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

    def config(self, db_name: str) -> GodooConfig:
        config_path = self.root / f"{db_name}.conf"
        addon_paths = [self.odoo_path / "odoo" / "addons", self.odoo_path / "addons"]
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
            "db",
            "--config",
            str(config.odoo_conf_path),
            "--data-dir",
            str(config.data_dir),
            "init",
            "--force",
            config.db_name,
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

    def execute(self, db_name: str, statement: str, parameters: Optional[tuple[object, ...]] = None) -> None:
        with self.connect(db_name) as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)

    def scalar(self, db_name: str, statement: str, parameters: Optional[tuple[object, ...]] = None) -> object:
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
    odoo_python = Path(os.environ.get("ODOO_PYTHON", "/odoo/venv/bin/python"))
    monkeypatch.setenv("PATH", f"{odoo_python.parent}:{os.environ['PATH']}")
    stack = LiveStack(
        root=tmp_path,
        python_path=odoo_python,
        odoo_path=Path(os.environ.get("ODOO_MAIN_FOLDER", "/odoo/odoo")),
        workspace_addons=Path(os.environ.get("ODOO_WORKSPACE_ADDON_LOCATION", "/odoo/godoo_workspace/addons")),
        thirdparty_addons=Path(os.environ.get("ODOO_THIRDPARTY_LOCATION", "/odoo/thirdparty")),
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
    db_name = live_stack.new_database_name("lifecycle")
    config = live_stack.config(db_name)
    after_reconcile_hooks = tmp_path / "after-reconcile"
    after_reconcile_hooks.mkdir()
    (after_reconcile_hooks / "20_second.py").write_text(
        "params = env['ir.config_parameter'].sudo()\n"
        "value = params.get_param('godoo.integration.lifecycle')\n"
        "assert value.endswith('pre-launch-10'), value\n"
        "params.set_param('godoo.integration.lifecycle', value + ',pre-launch-20')\n"
        "env.cr.commit()\n"
    )
    (after_reconcile_hooks / "10_first.py").write_text(
        "params = env['ir.config_parameter'].sudo()\n"
        "value = params.get_param('godoo.integration.lifecycle', '')\n"
        "separator = ',' if value else ''\n"
        "params.set_param('godoo.integration.lifecycle', value + separator + 'pre-launch-10')\n"
        "env.cr.commit()\n"
    )

    def ensure(conf: GodooConfig) -> bool:
        return ensure_runtime(
            conf,
            preparer=lambda _config: None,
            bootstrapper=live_stack.initialize,
            install_workspace_modules=False,
        )

    def reconcile(conf: GodooConfig) -> int:
        return reconcile_runtime(conf, preparer=lambda _config: None)

    result = deployment_init(
        config,
        seed_requested=False,
        seeder=None,
        ensure=ensure,
        reconciler=reconcile,
        after_reconcile_dirs=[after_reconcile_hooks],
        hook_runner=lifecycle_commands._run_hook,
    )

    assert result == (LifecycleOutcome.BOOTSTRAPPED, 0)
    assert (
        live_stack.scalar(
            db_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.lifecycle'",
        )
        == "pre-launch-10,pre-launch-20"
    )

    result = deployment_init(
        config,
        seed_requested=False,
        seeder=None,
        ensure=ensure,
        reconciler=reconcile,
        after_reconcile_dirs=[after_reconcile_hooks],
        hook_runner=lifecycle_commands._run_hook,
    )

    assert result == (LifecycleOutcome.READY, 0)
    assert (
        live_stack.scalar(
            db_name,
            "SELECT value FROM ir_config_parameter WHERE key = 'godoo.integration.lifecycle'",
        )
        == "pre-launch-10,pre-launch-20,pre-launch-10,pre-launch-20"
    )


def test_real_lifecycle_seeds_database_and_filestore_from_odoo_archive(live_stack: LiveStack) -> None:
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
            db_name=conf.db_name,
            archive_path=archive,
            odoo_bin_path=live_stack.odoo_path / "odoo-bin",
            odoo_conf_path=conf.odoo_conf_path,
            data_dir=conf.data_dir,
            force=True,
        )
        assert result == 0

    result = deployment_init(
        target_config,
        seed_requested=True,
        seeder=seed,
        ensure=lambda _config: pytest.fail("seeded runtime must not bootstrap"),
        reconciler=lambda config: reconcile_runtime(config, preparer=lambda _config: None),
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

    restore_custom_runtime(
        connection=target_config.db_connection,
        db_template="template0",
        dump_path=dump,
        filestore_source=source_filestore,
        data_dir=live_stack.data_dir,
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


def test_real_reset_replaces_and_drops_database_and_filestore(live_stack: LiveStack) -> None:
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
        db_name=target_name,
        db_template_name=template_name,
        odoo_bin_path=live_stack.odoo_path / "odoo-bin",
        odoo_conf_path=target_config.odoo_conf_path,
        data_dir=live_stack.data_dir,
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
        db_name=target_name,
        odoo_bin_path=live_stack.odoo_path / "odoo-bin",
        odoo_conf_path=target_config.odoo_conf_path,
        data_dir=live_stack.data_dir,
    )

    assert result == 0
    assert not live_stack.database_exists(target_name)
    assert not target_filestore.exists()
