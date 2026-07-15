"""EAOS end-to-end happy path: health → invoke → evolution run → status.

Usage::

    python scripts/happy_path.py

Requires the full stack to be running (``make up && make migrate && make seed``
then ``make serve`` or ``docker compose up api``). Reads EAOS_SECRET_KEY from
the environment for JWT minting.

Exits 0 on success, 1 on any assertion failure or connection error.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

import httpx

API_URL = os.environ.get("EAOS_API_URL", "http://localhost:8000")
SECRET = os.environ.get("EAOS_SECRET_KEY", "test-secret")

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")

INVOKE_MESSAGE = "你好，介绍一下你自己"
BASE_MODEL = os.environ.get("EAOS_LLM__DEFAULT_MODEL", "gpt-4o-mini")
POLL_TIMEOUT = 300  # seconds to wait for evolution stage change


def _admin_token() -> str:
    from eaos.core.auth import create_jwt_token

    return create_jwt_token(
        secret=SECRET, user_id=USER_ADMIN, tenant_id=TID, role="admin"
    )


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


async def _check_health(client: httpx.AsyncClient) -> None:
    print("1. Health check...")
    resp = await client.get("/health")
    if resp.status_code != 200:
        _fail(f"/health returned {resp.status_code}")
    _ok(f"/health → {resp.json()}")


async def _invoke(client: httpx.AsyncClient, token: str) -> None:
    print("2. POST /invoke (SSE stream)...")
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "agent_id": str(AGENT_PERSONAL),
        "message": INVOKE_MESSAGE,
        "session_id": str(SESSION_DEMO),
    }

    event_count = 0
    has_final = False
    async with client.stream("POST", "/invoke", json=body, headers=headers) as resp:
        if resp.status_code != 200:
            _fail(f"/invoke returned {resp.status_code}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            event_count += 1
            if '"final"' in payload:
                has_final = True

    if event_count == 0:
        _fail("no SSE events received")
    if not has_final:
        _fail(f"no 'final' event in {event_count} events")
    _ok(f"received {event_count} events, final event present")


async def _evolution_run(client: httpx.AsyncClient, token: str) -> str:
    print("3. POST /admin/evolution/run...")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/admin/evolution/run",
        json={"base_model": BASE_MODEL},
        headers=headers,
    )
    if resp.status_code != 200:
        _fail(f"/admin/evolution/run returned {resp.status_code}: {resp.text}")
    data = resp.json()
    run_id = str(data.get("run_id", data.get("id", "")))
    _ok(f"evolution run started: {run_id}")
    return run_id


async def _poll_status(client: httpx.AsyncClient, token: str) -> None:
    print("4. Polling /admin/evolution/status...")
    headers = {"Authorization": f"Bearer {token}"}
    initial_stage: str | None = None

    elapsed = 0.0
    interval = 5.0
    while elapsed < POLL_TIMEOUT:
        resp = await client.get("/admin/evolution/status", headers=headers)
        if resp.status_code != 200:
            _fail(f"/admin/evolution/status returned {resp.status_code}")
        data = resp.json()
        stage = data.get("stage", "unknown")

        if initial_stage is None:
            initial_stage = stage
            print(f"  initial stage: {initial_stage}")

        if stage in ("full", "blocked", "rejected", "approved"):
            _ok(f"evolution reached terminal stage: {stage}")
            return

        await asyncio.sleep(interval)
        elapsed += interval

    _fail(f"evolution did not reach terminal stage within {POLL_TIMEOUT}s "
          f"(stuck at {initial_stage})")


async def main() -> None:
    print(f"EAOS Happy Path — targeting {API_URL}")
    print()

    token = _admin_token()

    async with httpx.AsyncClient(base_url=API_URL, timeout=60.0) as client:
        await _check_health(client)
        await _invoke(client, token)
        await _evolution_run(client, token)
        await _poll_status(client, token)

    print()
    print("Happy path completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
