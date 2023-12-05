import typer

from .dump import dump_instance
from .load import load_instance_data
from .pull import InstancePuller


def backup_cli_app():
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions around Backing up and Restoring Odoo",
    )
    puller = InstancePuller()

    app.command("pull")(puller.pull_instance_data)
    app.command("load")(load_instance_data)
    app.command("dump")(dump_instance)

    return app
