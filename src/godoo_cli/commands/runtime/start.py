"""Prepare and launch one resolved runtime on every server process start."""

import inspect
import logging
from dataclasses import fields
from typing import Any, cast

from ...models import GodooConfig
from ...runtime.odoo import _extra_args_argv
from ..common import CommonCLI
from ..configuration import resolve_command_config
from .init import InitOptions, deployment_init_odoo_runtime, initialize_odoo_runtime
from .launch import launch_odoo, launch_prepared_runtime

LOGGER = logging.getLogger(__name__)
CLI = CommonCLI()
_CONFIG_OPTIONS = tuple(
    name for name in inspect.signature(resolve_command_config).parameters if name not in {"arguments", "extra"}
)
_INIT_OPTIONS = tuple(field.name for field in fields(InitOptions) if field.name != "if_needed")
_LAUNCH_OPTIONS = tuple(name for name in inspect.signature(launch_prepared_runtime).parameters if name != "godoo_conf")


def start_odoo_runtime(**options: Any) -> int:
    """Initialize explicitly, then launch only after the runtime is ready."""
    config_options = {name: options[name] for name in _CONFIG_OPTIONS}
    config: GodooConfig | None = None

    def resolve_config() -> GodooConfig:
        nonlocal config
        config = resolve_command_config(
            **config_options,
            arguments=_extra_args_argv(options.get("extra_args") or []),
            extra={"multithread_worker_count": options["multithread_worker_count"], "languages": options["languages"]},
        )
        return config

    init_options = {name: options[name] for name in _INIT_OPTIONS}
    LOGGER.info("Preparing runtime before server startup")
    result = initialize_odoo_runtime(resolve_config, InitOptions(**init_options, if_needed=False))
    if result:
        return CLI.returner(result)
    assert config is not None
    launch_options = {name: options[name] for name in _LAUNCH_OPTIONS}
    return CLI.returner(launch_prepared_runtime(config, **launch_options))


# Compose the existing public option declarations instead of maintaining a third CLI catalog.
_parameters = dict(inspect.signature(deployment_init_odoo_runtime).parameters)
_parameters.pop("if_needed")
for _name, _parameter in inspect.signature(launch_odoo).parameters.items():
    _parameters.setdefault(_name, _parameter)
cast(Any, start_odoo_runtime).__signature__ = inspect.Signature(list(_parameters.values()), return_annotation=int)
start_odoo_runtime.__annotations__ = {name: parameter.annotation for name, parameter in _parameters.items()} | {
    "return": int
}
