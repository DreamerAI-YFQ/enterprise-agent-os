"""Evolution CLI commands — trigger runs, check status, rollback via HTTP API."""

from __future__ import annotations

from typing import Annotated

import typer

from eaos_cli.client import ApiClient, print_json

evolution_app = typer.Typer(help="Evolution operations: run, status, rollback.")

ApiUrlOption = Annotated[
    str,
    typer.Option("--api-url", envvar="EAOS_API_URL", help="API server URL"),
]
TokenOption = Annotated[
    str,
    typer.Option("--token", envvar="EAOS_SERVICE_TOKEN", help="Bearer token"),
]


@evolution_app.command("run")
def run(
    base_model: Annotated[
        str, typer.Argument(help="Base model name for DPO training")
    ],
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Trigger an evolution cycle (feedback -> dataset -> training -> governance)."""
    result = ApiClient(api_url, token).post(
        "/admin/evolution/run",
        json_body={"base_model": base_model},
    )
    print_json(result)


@evolution_app.command("status")
def status(
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Get current evolution pipeline status."""
    result = ApiClient(api_url, token).get("/admin/evolution/status")
    print_json(result)


@evolution_app.command("strategies")
def strategies(
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """List all evolution strategies."""
    result = ApiClient(api_url, token).get("/admin/evolution/strategies")
    print_json(result)


@evolution_app.command("canary")
def canary(
    strategy_id: Annotated[
        str, typer.Argument(help="Strategy UUID to advance to canary")
    ],
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Advance a strategy from approval to canary stage."""
    result = ApiClient(api_url, token).post(
        f"/admin/evolution/strategies/{strategy_id}/canary"
    )
    print_json(result)


@evolution_app.command("rollback")
def rollback(
    strategy_id: Annotated[
        str, typer.Argument(help="Strategy UUID to roll back")
    ],
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Roll back a strategy to 'rolled_back' stage."""
    result = ApiClient(api_url, token).post(
        f"/admin/evolution/strategies/{strategy_id}/rollback"
    )
    print_json(result)
