import typer

from .passwords import set_passwords


def db_cli_app():
    app = typer.Typer(
        no_args_is_help=True,
        help="Functions that directly act on the Postgres DB",
    )

    app.command()(set_passwords)

    return app
