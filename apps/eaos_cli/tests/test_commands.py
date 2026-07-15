"""T3 CLI tests — command parsing, subprocess invocation, HTTP API calls."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

from eaos_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


class TestHelp:
    def test_help_lists_top_level_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("migrate", "seed", "serve", "worker", "admin", "evolution"):
            assert cmd in result.output

    def test_admin_help_lists_subcommands(self) -> None:
        result = runner.invoke(app, ["admin", "--help"])
        assert result.exit_code == 0
        for cmd in ("list-approvals", "approve", "reject"):
            assert cmd in result.output

    def test_evolution_help_lists_subcommands(self) -> None:
        result = runner.invoke(app, ["evolution", "--help"])
        assert result.exit_code == 0
        for cmd in ("run", "status", "rollback", "strategies", "canary"):
            assert cmd in result.output


class TestSubprocessCommands:
    @patch("eaos_cli.main.subprocess.run")
    def test_migrate_calls_alembic(self, mock_run: Mock) -> None:
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call: Any = mock_run.call_args
        cmd: list[str] = call[0][0]
        assert "alembic" in cmd
        assert "upgrade" in cmd
        assert "head" in cmd

    @patch("eaos_cli.main.subprocess.run")
    def test_seed_calls_seed_module(self, mock_run: Mock) -> None:
        result = runner.invoke(app, ["seed"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call: Any = mock_run.call_args
        cmd: list[str] = call[0][0]
        assert "eaos.infra.db.seed" in cmd

    @patch("eaos_cli.main.subprocess.run")
    def test_serve_calls_uvicorn_with_host_port(self, mock_run: Mock) -> None:
        result = runner.invoke(
            app, ["serve", "--host", "0.0.0.0", "--port", "9000"]
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call: Any = mock_run.call_args
        cmd: list[str] = call[0][0]
        assert "uvicorn" in cmd
        assert "eaos_api.main:app" in cmd
        assert "9000" in cmd

    @patch("eaos_cli.main.subprocess.run")
    def test_worker_calls_eaos_worker_module(self, mock_run: Mock) -> None:
        result = runner.invoke(app, ["worker"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call: Any = mock_run.call_args
        cmd: list[str] = call[0][0]
        assert "eaos_worker" in cmd


class TestAdminCommands:
    @patch("eaos_cli.commands.admin.ApiClient")
    def test_list_approvals_calls_get(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.get.return_value = [{"id": "abc", "status": "pending"}]
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["admin", "list-approvals"])

        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("/admin/approvals")
        assert "abc" in result.output

    @patch("eaos_cli.commands.admin.ApiClient")
    def test_approve_calls_post_approve(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "abc", "status": "approved"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["admin", "approve", "abc-123"])

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/approvals/abc-123/approve"
        )

    @patch("eaos_cli.commands.admin.ApiClient")
    def test_reject_with_reason_sends_body(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "abc", "status": "rejected"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app, ["admin", "reject", "abc-123", "--reason", "bad model"]
        )

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/approvals/abc-123/reject",
            json_body={"reason": "bad model"},
        )

    @patch("eaos_cli.commands.admin.ApiClient")
    def test_reject_without_reason_sends_empty_body(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "abc", "status": "rejected"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["admin", "reject", "abc-123"])

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/approvals/abc-123/reject",
            json_body={},
        )


class TestEvolutionCommands:
    @patch("eaos_cli.commands.evolution.ApiClient")
    def test_run_sends_base_model(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "run-1", "status": "queued"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["evolution", "run", "gpt-4"])

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/evolution/run",
            json_body={"base_model": "gpt-4"},
        )

    @patch("eaos_cli.commands.evolution.ApiClient")
    def test_status_calls_get(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.get.return_value = {"stage": "training"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["evolution", "status"])

        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("/admin/evolution/status")

    @patch("eaos_cli.commands.evolution.ApiClient")
    def test_strategies_calls_get(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.get.return_value = [{"id": "strat-1"}]
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["evolution", "strategies"])

        assert result.exit_code == 0
        mock_client.get.assert_called_once_with("/admin/evolution/strategies")

    @patch("eaos_cli.commands.evolution.ApiClient")
    def test_canary_calls_post(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "strat-1", "stage": "canary"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["evolution", "canary", "strat-1"])

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/evolution/strategies/strat-1/canary"
        )

    @patch("eaos_cli.commands.evolution.ApiClient")
    def test_rollback_calls_post(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = Mock()
        mock_client.post.return_value = {"id": "strat-1", "stage": "rolled_back"}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(app, ["evolution", "rollback", "strat-1"])

        assert result.exit_code == 0
        mock_client.post.assert_called_once_with(
            "/admin/evolution/strategies/strat-1/rollback"
        )
