"""Fail-closed contract tests for the competition evaluation CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

COMPETITION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPETITION_ROOT))

from runners import run_eval  # noqa: E402
from runners.metrics import compute_safety_metrics  # noqa: E402


def _configure_artifact_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_eval, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(run_eval, "EVIDENCE_ROOT", tmp_path / "evidence")


def _passing_safety_results() -> list[dict[str, object]]:
    return [
        {
            "case_id": case["case_id"],
            "attack_type": case.get("attack_type", case.get("category")),
            "expected_result": case.get("expected_result", "blocked"),
            "actual_result": "blocked",
            "g0_safety": True,
            "expected_match": True,
            "case_passed": True,
            "side_effects": {"status": "passed"},
            "canary_detected": False,
        }
        for case in run_eval.load_dataset("safety_attacks_v1.yaml")
    ]


def _write_passing_safety_artifacts(
    results_dir: Path,
    results: list[dict[str, object]],
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "safety_fixture_manifest.json").write_text(
        json.dumps(
            {
                "prepared": True,
                "fixture_error": None,
                "cleanup_attempted": True,
                "cleanup_succeeded": True,
                "cleanup_verification": {
                    "fixture_tenants": 0,
                    "fixture_sessions": 0,
                    "fixture_approvals": 0,
                },
                "cleanup_error": None,
            }
        ),
        encoding="utf-8",
    )
    (results_dir / "safety_metrics.json").write_text(
        json.dumps(compute_safety_metrics(results)),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "run_id",
    ["", ".", "..", "../escape", "nested/run", r"nested\run", "bad id"],
)
def test_validate_run_id_rejects_unsafe_identifiers(run_id: str) -> None:
    with pytest.raises(ValueError):
        run_eval.validate_run_id(run_id)


def test_agent_lookup_uses_tenant_parameter_and_returns_real_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="00000000-0000-0000-0000-000000000301\n",
            stderr="",
        )

    monkeypatch.delenv("EAOS_AGENT_ID", raising=False)
    monkeypatch.setattr(run_eval.subprocess, "run", fake_run)

    assert run_eval._resolve_default_agent_id() == "00000000-0000-0000-0000-000000000301"
    command = captured["command"]
    assert isinstance(command, list)
    assert "tenant_slug=acme-corp" in command
    assert command[-2:] == ["-f", "-"]
    assert "t.slug = :'tenant_slug'" in str(captured["input"])
    assert captured["check"] is False


def test_agent_lookup_fails_closed_on_psql_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EAOS_AGENT_ID", raising=False)
    monkeypatch.setattr(
        run_eval.subprocess,
        "run",
        MagicMock(return_value=SimpleNamespace(returncode=2, stdout="", stderr="db down")),
    )

    with pytest.raises(RuntimeError, match="active agent lookup failed: db down"):
        run_eval._resolve_default_agent_id()


def test_agent_lookup_fails_closed_on_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EAOS_AGENT_ID", raising=False)
    monkeypatch.setattr(
        run_eval.subprocess,
        "run",
        MagicMock(return_value=SimpleNamespace(returncode=0, stdout="\n", stderr="")),
    )

    with pytest.raises(RuntimeError, match="returned 0 rows"):
        run_eval._resolve_default_agent_id()


def test_configured_agent_is_still_verified_and_never_randomly_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = "00000000-0000-0000-0000-000000000399"
    runner = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("EAOS_AGENT_ID", configured)
    monkeypatch.setattr(run_eval.subprocess, "run", runner)

    with pytest.raises(RuntimeError, match="returned 0 rows"):
        run_eval._resolve_default_agent_id()
    command = runner.call_args.args[0]
    assert f"agent_id={configured}" in command
    assert "a.id = :'agent_id'::uuid" in runner.call_args.kwargs["input"]


async def test_agent_resolution_failure_happens_before_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_eval, "DEFAULT_AGENT_ID", None)
    monkeypatch.setattr(
        run_eval,
        "_resolve_default_agent_id",
        MagicMock(side_effect=RuntimeError("no active tenant agent")),
    )
    client = MagicMock()

    with pytest.raises(RuntimeError, match="no active tenant agent"):
        await run_eval.invoke_agent_sse(client, "token", "message")
    client.stream.assert_not_called()


async def test_unauthenticated_invoke_omits_empty_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_eval,
        "DEFAULT_AGENT_ID",
        "00000000-0000-0000-0000-000000000111",
    )
    response = MagicMock()
    response.status_code = 401
    response.headers = {}
    response.aread = AsyncMock(return_value=b'{"detail":"Not authenticated"}')
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=None)
    client = MagicMock()
    client.stream.return_value = context

    result = await run_eval.invoke_agent_sse(
        client,
        "",
        "unauthenticated write attempt",
        max_retries=0,
    )

    assert result["status_code"] == 401
    assert client.stream.call_args.kwargs["headers"] == {}


def test_prepare_run_directories_refuses_stale_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_artifact_roots(monkeypatch, tmp_path)
    stale = tmp_path / "results" / "existing-run"
    stale.mkdir(parents=True)
    (stale / "old.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="already has artifacts"):
        run_eval.prepare_run_directories("existing-run")


def test_complete_safety_gate_requires_fixtures_cleanup_metrics_and_60_cases(
    tmp_path: Path,
) -> None:
    results = _passing_safety_results()
    results_dir = tmp_path / "safety"
    _write_passing_safety_artifacts(results_dir, results)

    assert run_eval.assess_safety_hard_gate(results, results_dir) == []

    results[-1]["case_passed"] = False
    reasons = run_eval.assess_safety_hard_gate(results, results_dir)
    assert any("59/60" in reason for reason in reasons)
    assert any(reason.startswith("G0:") for reason in reasons)


async def test_main_returns_nonzero_and_records_export_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_artifact_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("EAOS_LLM__DEFAULT_MODEL", "generation-test-model")
    monkeypatch.setenv("EAOS_EMBEDDING__MODEL", "embedding-test-model")
    monkeypatch.setattr(
        run_eval,
        "get_tokens",
        AsyncMock(return_value={"admin": "a", "employee": "e"}),
    )
    monkeypatch.setattr(run_eval, "run_rag_suite", AsyncMock(return_value=[]))
    export = MagicMock(return_value=SimpleNamespace(returncode=7))
    monkeypatch.setattr(run_eval.subprocess, "run", export)

    exit_code = await run_eval.main("rag", "export-failure-test")

    assert exit_code == 1
    metadata = json.loads(
        (tmp_path / "results" / "export-failure-test" / "run_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["run_passed"] is False
    assert metadata["evidence_export"] == {
        "attempted": True,
        "succeeded": False,
        "returncode": 7,
    }
    assert any("return code 7" in reason for reason in metadata["failure_reasons"])
    command = export.call_args.args[0]
    assert command[command.index("--model") + 1] == "generation-test-model"
    assert command[command.index("--embedding-model") + 1] == "embedding-test-model"


async def test_main_returns_nonzero_for_partial_safety_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_artifact_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        run_eval,
        "get_tokens",
        AsyncMock(return_value={"admin": "a", "employee": "e"}),
    )
    monkeypatch.setattr(run_eval, "run_safety_suite", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        run_eval.subprocess,
        "run",
        MagicMock(return_value=SimpleNamespace(returncode=0)),
    )

    exit_code = await run_eval.main("safety", "partial-safety-test", limit=1)

    assert exit_code == 1
    metadata = json.loads(
        (tmp_path / "results" / "partial-safety-test" / "run_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["run_passed"] is False
    assert any("0/60" in reason for reason in metadata["failure_reasons"])
