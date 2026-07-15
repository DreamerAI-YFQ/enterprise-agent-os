"""EAOS management CLI.

Usage::

    uv run eaos migrate        # run database migrations
    uv run eaos seed           # load demo seed data
    uv run eaos serve          # start API server (uvicorn)
    uv run eaos worker         # start evolution worker
    uv run eaos admin list-approvals
    uv run eaos admin approve <id>
    uv run eaos evolution run <base_model>
    uv run eaos evolution status
"""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

from eaos_cli.commands.admin import admin_app
from eaos_cli.commands.evolution import evolution_app

app = typer.Typer(
    name="eaos",
    help="EAOS management CLI — migrate, seed, serve, worker, admin, evolution.",
    no_args_is_help=True,
)
app.add_typer(admin_app, name="admin")
app.add_typer(evolution_app, name="evolution")


@app.command()
def migrate() -> None:
    """Run alembic upgrade head."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
    )


@app.command()
def seed() -> None:
    """Load demo seed data."""
    subprocess.run(
        [sys.executable, "-m", "eaos.infra.db.seed"],
        check=True,
    )


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host")] = "0.0.0.0",
    port: Annotated[int, typer.Option(help="Bind port")] = 8000,
) -> None:
    """Start the API server (uvicorn)."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "eaos_api.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        check=True,
    )


@app.command()
def worker() -> None:
    """Start the evolution worker process."""
    subprocess.run(
        [sys.executable, "-m", "eaos_worker"],
        check=True,
    )


def main() -> None:
    """Entry point for the eaos CLI script."""
    app()


if __name__ == "__main__":
    main()
