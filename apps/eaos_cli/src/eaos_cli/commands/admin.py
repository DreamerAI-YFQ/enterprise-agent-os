"""Admin CLI commands — approval management via HTTP API."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from eaos_cli.client import ApiClient, print_json

admin_app = typer.Typer(help="Admin operations: approvals, policies.")

ApiUrlOption = Annotated[
    str,
    typer.Option("--api-url", envvar="EAOS_API_URL", help="API server URL"),
]
TokenOption = Annotated[
    str,
    typer.Option("--token", envvar="EAOS_SERVICE_TOKEN", help="Bearer token"),
]


@admin_app.command("list-approvals")
def list_approvals(
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """List pending approvals."""
    result = ApiClient(api_url, token).get("/admin/approvals")
    print_json(result)


@admin_app.command("approve")
def approve(
    approval_id: Annotated[str, typer.Argument(help="Approval UUID")],
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Approve a pending approval."""
    result = ApiClient(api_url, token).post(
        f"/admin/approvals/{approval_id}/approve"
    )
    print_json(result)


@admin_app.command("reject")
def reject(
    approval_id: Annotated[str, typer.Argument(help="Approval UUID")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Rejection reason"),
    ] = "",
    api_url: ApiUrlOption = "http://localhost:8000",
    token: TokenOption = "",
) -> None:
    """Reject a pending approval."""
    body: dict[str, Any] = {}
    if reason:
        body["reason"] = reason
    result = ApiClient(api_url, token).post(
        f"/admin/approvals/{approval_id}/reject",
        json_body=body,
    )
    print_json(result)
