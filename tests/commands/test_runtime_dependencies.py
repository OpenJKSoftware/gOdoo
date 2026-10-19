"""Tests source-only runtime dependency planning and installation."""

from __future__ import annotations

from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from godoo_cli.commands.root import main_cli
from godoo_cli.runtime.odoo import (
    install_dependency_requirements,
    source_dependency_requirements,
    workspace_module_names,
)


def manifest(
    root: Path,
    name: str,
    *,
    depends: list[str] | None = None,
    python: list[str] | None = None,
    installable: bool = True,
    version: str | None = None,
) -> None:
    """Create one minimal addon manifest."""
    addon = root / name
    addon.mkdir(parents=True)
    data = {
        "name": name,
        "depends": depends or [],
        "installable": installable,
        "external_dependencies": {"python": python or []},
    }
    if version:
        data["version"] = version
    (addon / "__manifest__.py").write_text(repr(data))


def release(odoo_path: Path) -> None:
    """Create source metadata and an executable that must never be invoked."""
    (odoo_path / "odoo").mkdir(parents=True)
    (odoo_path / "odoo" / "release.py").write_text("FINAL = 0\nversion_info = (19, 0, 1, FINAL, 0)\n", encoding="utf-8")
    (odoo_path / "odoo-bin").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")


def test_source_dependency_requirements_resolve_closure_and_keep_constraints(tmp_path: Path) -> None:
    """Manifest strings remain exact while duplicates are removed in discovery order."""
    addons = tmp_path / "addons"
    manifest(addons, "base", python=["base>=1"])
    manifest(addons, "dep", python=["pkg>=2; python_version < '3.13'", "ldap"])
    manifest(addons, "sale", depends=["dep", "base"], python=["pkg @ https://example.test/pkg.whl", "ldap"])

    assert source_dependency_requirements([addons], ["sale"]) == [
        "pkg @ https://example.test/pkg.whl",
        "python-ldap",
        "pkg>=2; python_version < '3.13'",
        "base>=1",
    ]


def test_source_dependency_requirements_with_no_seeds_ignores_available_addons(tmp_path: Path) -> None:
    """An empty selection does not discover requirements from every available addon."""
    addons = tmp_path / "addons"
    manifest(addons, "unselected", python=["unselected-pkg"])

    assert source_dependency_requirements([addons], []) == []


def test_workspace_module_names_filters_installable_and_other_odoo_majors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default workspace seeds are installable modules for the selected Odoo major."""
    addons = tmp_path / "addons"
    manifest(addons, "current", version="19.0.1.0")
    manifest(addons, "disabled", installable=False, version="19.0.1.0")
    manifest(addons, "old", version="18.0.1.0")
    manifest(addons, "unversioned")

    from godoo_cli.runtime import odoo

    get_modules = odoo.GodooModules.get_modules
    monkeypatch.setattr(
        odoo.GodooModules,
        "get_modules",
        lambda registry: sorted(get_modules(registry), key=lambda module: module.name, reverse=True),
    )

    assert workspace_module_names(addons, 19) == ["current", "unversioned"]


@pytest.mark.parametrize("workspace_state", ["empty", "disabled", "old"])
def test_dependencies_command_default_empty_seeds_keeps_core_and_extra_requirements(
    tmp_path: Path, workspace_state: str
) -> None:
    """Default planning leaves unrelated addon requirements out when no module qualifies."""
    odoo = tmp_path / "odoo"
    release(odoo)
    (odoo / "requirements.txt").write_text("odoo-core\n")
    workspace = tmp_path / "addons"
    workspace.mkdir()
    if workspace_state == "disabled":
        manifest(workspace, "disabled", installable=False, python=["disabled-pkg"], version="19.0.1.0")
    elif workspace_state == "old":
        manifest(workspace, "old", python=["old-pkg"], version="18.0.1.0")
    extra = tmp_path / "extra-requirements.txt"
    extra.write_text("extra-pkg\n")

    result = CliRunner().invoke(
        main_cli(),
        [
            "runtime",
            "dependencies",
            "--odoo-path",
            str(odoo),
            "--workspace-addons",
            str(workspace),
            "--addon-path",
            str(workspace),
            "--requirements",
            str(extra),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"-r {odoo / 'requirements.txt'}", f"-r {extra}"]


def test_dependencies_command_default_seeds_include_generic_and_selected_major_versions(tmp_path: Path) -> None:
    """Default planning accepts Odoo's generic two- and three-part addon versions."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    manifest(workspace, "selected", python=["selected-pkg"], version="19.0.1.0")
    manifest(workspace, "generic_two", python=["generic-two-pkg"], version="1.0")
    manifest(workspace, "generic_three", python=["generic-three-pkg"], version="1.2.3")
    manifest(workspace, "wrong_major", python=["wrong-major-pkg"], version="18.0.1.0")
    manifest(workspace, "disabled", installable=False, python=["disabled-pkg"], version="1.0")

    result = CliRunner().invoke(
        main_cli(),
        [
            "runtime",
            "dependencies",
            "--odoo-path",
            str(odoo),
            "--workspace-addons",
            str(workspace),
            "--addon-path",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert set(result.output.splitlines()) == {"selected-pkg", "generic-two-pkg", "generic-three-pkg"}


def test_dependencies_command_plans_explicit_modules_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command plans explicit module closure without runtime database access."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    manifest(workspace, "dep", python=["dep>=1; python_version >= '3.11'"])
    manifest(workspace, "sale", depends=["dep"], python=["sale @ https://example.test/sale.whl"])
    (odoo / "requirements.txt").parent.mkdir(parents=True, exist_ok=True)
    (odoo / "requirements.txt").write_text("odoo-core\n")
    monkeypatch.setattr("godoo_cli.runtime.odoo.run_odoo_command", lambda *_args, **_kwargs: pytest.fail("ran odoo"))

    result = CliRunner().invoke(
        main_cli(),
        [
            "runtime",
            "dependencies",
            "--odoo-path",
            str(odoo),
            "--workspace-addons",
            str(workspace),
            "--addon-path",
            str(workspace),
            "--module",
            "sale",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"-r {odoo / 'requirements.txt'}",
        "sale @ https://example.test/sale.whl",
        "dep>=1; python_version >= '3.11'",
    ]
    assert not list(odoo.rglob("__pycache__"))


def test_dependencies_command_installs_once_with_selected_python(tmp_path: Path) -> None:
    """Installation is one additive uv call with source and extra requirements."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    extra = tmp_path / "upgrade-requirements.txt"
    extra.touch()
    manifest(workspace, "sale", python=["sale-pkg"])
    (odoo / "requirements.txt").parent.mkdir(parents=True, exist_ok=True)
    (odoo / "requirements.txt").touch()
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        result = CliRunner().invoke(
            main_cli(),
            [
                "runtime",
                "dependencies",
                "--odoo-path",
                str(odoo),
                "--workspace-addons",
                str(workspace),
                "--addon-path",
                str(workspace),
                "--module",
                "sale",
                "--requirements",
                str(extra),
                "--install",
                "--python",
                str(tmp_path / "venv/bin/python"),
                "--no-cache",
            ],
        )

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(tmp_path / "venv/bin/python"),
            "--no-cache",
            "-r",
            str(odoo / "requirements.txt"),
            "sale-pkg",
            "-r",
            str(extra),
        ],
        check=True,
    )


def test_install_dependency_requirements_keeps_source_requirement_when_core_is_preinstalled(tmp_path: Path) -> None:
    """The standalone installer always supplies the source requirements constraint."""
    requirements = tmp_path / "requirements.txt"
    requirements.touch()
    with patch("godoo_cli.runtime.odoo.subprocess.run") as run:
        install_dependency_requirements(tmp_path / "python", ["addon-pkg"], odoo_requirements=requirements)

    assert "-r" in run.call_args.args[0]


def test_dependencies_command_requires_python_only_for_install(tmp_path: Path) -> None:
    """A source-only plan has no interpreter target, while installation does."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    manifest(workspace, "sale", python=["sale-pkg"])

    result = CliRunner().invoke(
        main_cli(),
        [
            "runtime",
            "dependencies",
            "--odoo-path",
            str(odoo),
            "--workspace-addons",
            str(workspace),
            "--addon-path",
            str(workspace),
            "--module",
            "sale",
            "--install",
        ],
    )

    assert result.exit_code == 2
    assert "--python is required with --install" in unstyle(result.output)


def test_dependencies_command_reports_uv_install_failure_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv failure becomes a concise CLI failure after uv has reported its own diagnostics."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    manifest(workspace, "sale", python=["sale-pkg"])
    monkeypatch.setattr(
        "godoo_cli.commands.runtime.dependencies.install_dependency_requirements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CalledProcessError(17, ["uv"])),
    )

    result = CliRunner().invoke(
        main_cli(),
        [
            "runtime",
            "dependencies",
            "--odoo-path",
            str(odoo),
            "--workspace-addons",
            str(workspace),
            "--addon-path",
            str(workspace),
            "--module",
            "sale",
            "--install",
            "--python",
            str(tmp_path / "venv/bin/python"),
        ],
    )

    assert result.exit_code == 17
    assert "Runtime dependency installation failed" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("option", ["--workspace-addons", "--requirements"])
def test_dependencies_command_rejects_missing_source_inputs(tmp_path: Path, option: str) -> None:
    """Source planning fails before output when a required local input is absent."""
    odoo = tmp_path / "odoo"
    release(odoo)
    workspace = tmp_path / "addons"
    manifest(workspace, "sale", python=["sale-pkg"])
    arguments = [
        "runtime",
        "dependencies",
        "--odoo-path",
        str(odoo),
        "--workspace-addons",
        str(workspace),
        "--addon-path",
        str(workspace),
        "--module",
        "sale",
    ]
    if option == "--workspace-addons":
        arguments[arguments.index("--workspace-addons") + 1] = str(tmp_path / "missing-addons")
        arguments = arguments[: arguments.index("--module")]
    else:
        arguments.extend(["--requirements", str(tmp_path / "missing-requirements.txt")])

    result = CliRunner().invoke(main_cli(), arguments)

    assert result.exit_code == 2
    assert option in unstyle(result.output)
