"""C11: Two main demo E2E tests.

Demo A: Knowledge contribution → review → ingest → cross-user reuse
Demo B: Natural language order → approval → write → audit → rollback

These tests verify the full closed-loop of both main demos.
Run with: python benchmarks/competition/runners/run_e2e.py [--demo A|B|both]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess as _sp
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "benchmarks" / "competition"))

from runners.run_eval import invoke_agent_sse  # type: ignore[import-not-found]  # noqa: E402

API_BASE = os.environ.get("EAOS_API_URL", "http://localhost:8000")
TENANT_SLUG = os.environ.get("EAOS_TENANT_SLUG", "acme-corp")
ADMIN_EMAIL = os.environ.get("EAOS_ADMIN_EMAIL", "admin@acme.com")
ADMIN_PASSWORD = os.environ.get("EAOS_ADMIN_PASSWORD", "EaosDemo-Admin-2026!")
EMPLOYEE_EMAIL = os.environ.get("EAOS_EMPLOYEE_EMAIL", "employee@acme.com")
EMPLOYEE_PASSWORD = os.environ.get(
    "EAOS_EMPLOYEE_PASSWORD", "EaosDemo-Employee-2026!"
)
MANAGER_EMAIL = os.environ.get("EAOS_MANAGER_EMAIL", "manager@acme.com")
MANAGER_PASSWORD = os.environ.get(
    "EAOS_MANAGER_PASSWORD", "EaosDemo-Manager-2026!"
)

# Fetch a real agent_id from the database
_agent_id_result = _sp.run(
    [
        "docker",
        "exec",
        "eaos-postgres",
        "psql",
        "-U",
        "eaos",
        "-d",
        "eaos",
        "-t",
        "-A",
        "-c",
        "SELECT id FROM agent.agents LIMIT 1",
    ],
    capture_output=True,
    text=True,
    timeout=10,
)
DEFAULT_AGENT_ID = _agent_id_result.stdout.strip() or str(uuid.uuid4())
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_output(*args: str) -> str:
    completed = _sp.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        f"{API_BASE}/api/auth/login",
        json={"tenant_slug": TENANT_SLUG, "email": email, "password": password},
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def _stream_api_sse(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Stream one API call and preserve event metadata plus session binding."""
    result: dict[str, Any] = {
        "status_code": 0,
        "session_id": None,
        "events": [],
        "error": None,
    }
    async with client.stream(
        "POST",
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=300,
    ) as response:
        result["status_code"] = response.status_code
        result["session_id"] = response.headers.get("x-session-id")
        if response.status_code != 200:
            body = await response.aread()
            result["error"] = body.decode("utf-8", errors="replace")[:500]
            return result
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw.strip() == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            result["events"].append(event)
            if event.get("type") == "error":
                result["error"] = event.get("content")
    return result


def _approval_event(stream: dict[str, Any]) -> dict[str, Any] | None:
    for event in stream.get("events", []):
        if event.get("type") == "approval_required":
            return event.get("metadata") or {}
    return None


def _final_content(stream: dict[str, Any]) -> str:
    for event in reversed(stream.get("events", [])):
        if event.get("type") == "final" and event.get("content"):
            return str(event["content"])
    return ""


def _write_outcome(stream: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the structured WriteOutcome emitted by mcp/approval nodes."""
    for event in reversed(stream.get("events", [])):
        metadata = event.get("metadata") or {}
        result = metadata.get("result") or {}
        content = result.get("content") or []
        if not content or not isinstance(content[0], dict):
            continue
        try:
            payload = json.loads(content[0].get("text", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and "success" in payload:
            return payload
    return None


# ---------------------------------------------------------------------------
# Demo A: Knowledge Contribution → Review → Ingest → Reuse
# ---------------------------------------------------------------------------


async def _legacy_demo_a_knowledge_loop() -> dict[str, Any]:
    """主演示 A: 知识贡献闭环.

    Steps:
    1. Employee contributes knowledge
    2. Admin reviews and approves
    3. Employee (or another user) queries → knowledge is retrieved
    4. Verify citation is present
    """
    print("\n" + "=" * 60)
    print("Demo A: Knowledge Contribution → Review → Reuse")
    print("=" * 60)

    result: dict[str, Any] = {"demo": "A", "steps": [], "passed": True}
    async with httpx.AsyncClient(timeout=300) as client:
        # Login
        admin_token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        employee_token = await login(client, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        employee_headers = {"Authorization": f"Bearer {employee_token}"}

        # Step 1: Employee contributes knowledge
        print("\n[Step 1] Employee contributes knowledge...")
        contribution_content = (
            "E2E测试贡献：产品PRD-001是企业级路由器，支持10Gbps吞吐量，"
            f"部署于核心网络节点。UUID标记：{uuid.uuid4().hex[:8]}"
        )
        resp = await client.post(
            f"{API_BASE}/api/knowledge/contributions",
            headers=employee_headers,
            json={
                "title": "E2E测试-PRD-001产品知识",
                "content": contribution_content,
                "category": "product",
                "tags": ["路由器", "PRD-001", "e2e"],
            },
        )
        step1: dict[str, Any] = {"step": 1, "name": "contribute"}
        if resp.status_code in (200, 201):
            contribution_id = resp.json().get("id")
            step1["status"] = "ok"
            step1["contribution_id"] = contribution_id
            print(f"  ✓ Contribution created: {contribution_id}")
        else:
            step1["status"] = "fail"
            step1["error"] = f"HTTP {resp.status_code}: {resp.text[:100]}"
            print(f"  ✗ Failed: {step1['error']}")
            result["passed"] = False
            result["steps"].append(step1)
            return result
        result["steps"].append(step1)

        # Step 2: Admin reviews and approves
        print("\n[Step 2] Admin reviews and approves...")
        await asyncio.sleep(1)  # Brief pause for DB consistency
        resp = await client.post(
            f"{API_BASE}/api/admin/contributions/{contribution_id}/review",
            headers=admin_headers,
            json={"decision": "approved", "reason": "E2E auto-approve: content verified"},
        )
        step2: dict[str, Any] = {"step": 2, "name": "approve"}
        if resp.status_code in (200, 204):
            step2["status"] = "ok"
            print("  ✓ Contribution approved")
        else:
            step2["status"] = "fail"
            step2["error"] = f"HTTP {resp.status_code}: {resp.text[:100]}"
            print(f"  ✗ Failed: {step2['error']}")
            result["passed"] = False
            result["steps"].append(step2)
            return result
        result["steps"].append(step2)

        # Step 3: Query the knowledge (should find the approved contribution)
        print("\n[Step 3] Employee queries knowledge...")
        await asyncio.sleep(2)  # Wait for indexing
        query = "PRD-001是什么产品？"
        sse = await invoke_agent_sse(client, employee_token, query)
        step3: dict[str, Any] = {"step": 3, "name": "query_reuse"}
        output = sse["final_content"] or ""
        step3["agent_response"] = output
        if sse["status_code"] == 200 and output:
            # Check if the contributed knowledge is found
            if "路由器" in output or "10Gbps" in output or "PRD-001" in output:
                step3["status"] = "ok"
                step3["knowledge_found"] = True
                print("  ✓ Knowledge retrieved successfully")
            else:
                step3["status"] = "partial"
                step3["knowledge_found"] = False
                step3["note"] = "Query succeeded but contributed knowledge not in response"
                print("  ~ Query succeeded but knowledge not found in response")
            # Check for citation
            if ("[" in output and "]" in output) or "来源" in output:
                step3["has_citation"] = True
                print("  ✓ Citation present")
            else:
                step3["has_citation"] = False
                print("  ~ No citation markers found")
        else:
            step3["status"] = "fail"
            step3["error"] = f"HTTP {sse['status_code']}: {sse['error']}"
            print("  ✗ Query failed")
            result["passed"] = False
        result["steps"].append(step3)

    # Summary
    # C13/Fix-6: 仅 "ok" 算 Pass；partial 表示步骤未完全成功（如知识未检索到、
    # 权限拦截、幂等未检测），不应判为整体通过。
    all_ok = all(s.get("status") == "ok" for s in result["steps"])
    result["passed"] = all_ok
    print(f"\nDemo A: {'PASS' if result['passed'] else 'FAIL'}")
    return result


# ---------------------------------------------------------------------------
# Demo B: Order → Approval → Write → Audit → Rollback
# ---------------------------------------------------------------------------


async def demo_a_knowledge_loop() -> dict[str, Any]:
    """Run the strict contribution-review-index-reuse evidence loop."""
    marker = f"ZFPL-{uuid.uuid4().hex[:12].upper()}"
    verification_code = f"EVID-{uuid.uuid4().hex[:10].upper()}"
    title = f"Competition evidence knowledge {marker}"
    content = (
        f"Organizational knowledge marker {marker} has verification code "
        f"{verification_code}. This synthetic fact exists only to verify the "
        "reviewed knowledge reuse loop."
    )
    query = f"What is the verification code for organizational marker {marker}?"
    result: dict[str, Any] = {
        "demo": "A",
        "steps": [],
        "marker": marker,
        "pending_hidden": False,
        "indexed_once": False,
        "cross_user_retrieval": False,
        "chat_citation_verified": False,
        "duplicate_review_blocked": False,
        "passed": False,
    }

    async with httpx.AsyncClient(timeout=300) as client:
        admin_token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        employee_token = await login(client, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD)
        manager_token = await login(client, MANAGER_EMAIL, MANAGER_PASSWORD)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        employee_headers = {"Authorization": f"Bearer {employee_token}"}
        manager_headers = {"Authorization": f"Bearer {manager_token}"}

        # 1. Employee submits a unique synthetic fact.
        response = await client.post(
            f"{API_BASE}/api/knowledge/contributions",
            headers=employee_headers,
            json={
                "title": title,
                "content": content,
                "metadata": {
                    "category": "competition_evidence",
                    "marker": marker,
                    "synthetic_fixture": True,
                },
            },
        )
        contribution = response.json() if response.status_code == 201 else {}
        contribution_id = contribution.get("id")
        submit_ok = bool(
            response.status_code == 201
            and contribution_id
            and contribution.get("status") == "pending"
        )
        result["steps"].append(
            {
                "step": 1,
                "name": "employee_submits_unique_fact",
                "status": "ok" if submit_ok else "fail",
                "contribution_id": contribution_id,
                "http_status": response.status_code,
            }
        )
        if not submit_ok:
            return result

        # 2. A different user must not retrieve pending content.
        pending_response = await client.post(
            f"{API_BASE}/api/knowledge/search",
            headers=manager_headers,
            json={"query": query, "top_k": 10},
        )
        pending_results = (
            pending_response.json() if pending_response.status_code == 200 else []
        )
        pending_hidden = bool(
            pending_response.status_code == 200
            and isinstance(pending_results, list)
            and all(
                marker not in str(item.get("content", ""))
                and verification_code not in str(item.get("content", ""))
                for item in pending_results
                if isinstance(item, dict)
            )
        )
        result["pending_hidden"] = pending_hidden
        result["steps"].append(
            {
                "step": 2,
                "name": "pending_content_not_retrievable",
                "status": "ok" if pending_hidden else "fail",
                "result_count": len(pending_results)
                if isinstance(pending_results, list)
                else None,
            }
        )
        if not pending_hidden:
            return result

        # 3. Administrator approves; indexing is synchronous and recoverable.
        review_response = await client.post(
            f"{API_BASE}/api/admin/contributions/{contribution_id}/review",
            headers=admin_headers,
            json={
                "decision": "approved",
                "reason": "competition Demo A synthetic fact verified",
            },
        )
        approved = review_response.json() if review_response.status_code == 200 else {}
        documents_response = await client.get(
            f"{API_BASE}/api/admin/knowledge/documents",
            headers=admin_headers,
            params={"limit": 200},
        )
        documents = (
            documents_response.json() if documents_response.status_code == 200 else []
        )
        linked_documents = [
            item
            for item in documents
            if isinstance(item, dict)
            and str((item.get("metadata") or {}).get("contribution_id"))
            == str(contribution_id)
        ]
        indexed_once = bool(
            approved.get("status") == "approved"
            and len(linked_documents) == 1
            and linked_documents[0].get("status") == "indexed"
        )
        document_id = linked_documents[0].get("id") if linked_documents else None
        result["indexed_once"] = indexed_once
        result["document_id"] = document_id
        result["contribution_id"] = contribution_id
        result["steps"].append(
            {
                "step": 3,
                "name": "admin_review_and_index",
                "status": "ok" if indexed_once else "fail",
                "contribution_status": approved.get("status"),
                "document_id": document_id,
                "linked_document_count": len(linked_documents),
            }
        )
        if not indexed_once:
            return result

        # 4. Another authorized user retrieves the exact indexed document.
        reuse_response = await client.post(
            f"{API_BASE}/api/knowledge/search",
            headers=manager_headers,
            json={"query": query, "top_k": 10},
        )
        reuse_results = reuse_response.json() if reuse_response.status_code == 200 else []
        exact_results = [
            item
            for item in reuse_results
            if isinstance(item, dict)
            and verification_code in str(item.get("content", ""))
            and str((item.get("metadata") or {}).get("document_id"))
            == str(document_id)
        ]
        cross_user_retrieval = bool(exact_results)
        result["cross_user_retrieval"] = cross_user_retrieval
        result["steps"].append(
            {
                "step": 4,
                "name": "cross_user_exact_retrieval",
                "status": "ok" if cross_user_retrieval else "fail",
                "matched_document_id": (
                    (exact_results[0].get("metadata") or {}).get("document_id")
                    if exact_results
                    else None
                ),
                "result_count": len(reuse_results)
                if isinstance(reuse_results, list)
                else None,
            }
        )
        if not cross_user_retrieval:
            return result

        # 5. The manager also reuses it through the real chat/RAG entry point.
        chat = await _stream_api_sse(
            client,
            f"{API_BASE}/api/invoke",
            manager_token,
            {"agent_id": DEFAULT_AGENT_ID, "message": query, "mode": "rag"},
        )
        answer = _final_content(chat)
        citation_verified = bool(
            chat["status_code"] == 200
            and not chat.get("error")
            and verification_code in answer
            and any(f"[{index}]" in answer for index in range(1, 11))
        )
        result["chat_citation_verified"] = citation_verified
        result["session_id"] = chat.get("session_id")
        result["steps"].append(
            {
                "step": 5,
                "name": "chat_reuse_with_citation",
                "status": "ok" if citation_verified else "fail",
                "session_id": chat.get("session_id"),
                "answer": answer,
                "error": chat.get("error"),
            }
        )
        if not citation_verified:
            return result

        # 6. A duplicate review is rejected and cannot create a second document.
        duplicate_response = await client.post(
            f"{API_BASE}/api/admin/contributions/{contribution_id}/review",
            headers=admin_headers,
            json={"decision": "approved", "reason": "duplicate review probe"},
        )
        documents_response = await client.get(
            f"{API_BASE}/api/admin/knowledge/documents",
            headers=admin_headers,
            params={"limit": 200},
        )
        final_documents = (
            documents_response.json() if documents_response.status_code == 200 else []
        )
        final_linked_count = sum(
            1
            for item in final_documents
            if isinstance(item, dict)
            and str((item.get("metadata") or {}).get("contribution_id"))
            == str(contribution_id)
        )
        duplicate_blocked = duplicate_response.status_code == 409 and final_linked_count == 1
        result["duplicate_review_blocked"] = duplicate_blocked
        result["steps"].append(
            {
                "step": 6,
                "name": "duplicate_review_does_not_duplicate_document",
                "status": "ok" if duplicate_blocked else "fail",
                "http_status": duplicate_response.status_code,
                "linked_document_count": final_linked_count,
            }
        )

    required = (
        "pending_hidden",
        "indexed_once",
        "cross_user_retrieval",
        "chat_citation_verified",
        "duplicate_review_blocked",
    )
    result["passed"] = all(result[key] is True for key in required)
    return result


async def _legacy_demo_b_order_loop() -> dict[str, Any]:
    """主演示 B: 订单写链闭环.

    Steps:
    1. Employee requests high-value order (triggers approval)
    2. Admin approves
    3. System writes to ERP
    4. Verify audit log entry exists
    5. Test rollback (or verify idempotency)
    """
    print("\n" + "=" * 60)
    print("Demo B: Order → Approval → Write → Audit → Rollback")
    print("=" * 60)

    result: dict[str, Any] = {"demo": "B", "steps": [], "passed": True}
    async with httpx.AsyncClient(timeout=300) as client:
        admin_token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        employee_token = await login(client, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        del employee_token

        # Step 1: Admin requests high-value order (triggers approval)
        # C13/Fix-B: Use admin (not employee) so write permission is granted
        # and the full approval→write→audit→rollback chain can be exercised.
        # C13/Fix-B3: Use real customer code / product sku from erp tables.
        print("\n[Step 1] Admin requests high-value order (qty=200)...")
        order_msg = "创建销售订单：客户 CUS-TECH-0001，产品 PRD-ELEC-001，数量 200，单价 5000"
        # Don't pass session_id — let API create a new session automatically
        sse = await invoke_agent_sse(client, admin_token, order_msg)
        step1: dict[str, Any] = {"step": 1, "name": "request_order"}
        output = sse["final_content"] or ""
        step1["agent_response"] = output
        if sse["status_code"] == 200:
            # Should trigger approval
            if "审批" in output or "approval" in output.lower() or "批准" in output:
                step1["status"] = "ok"
                step1["approval_triggered"] = True
                print("  ✓ Approval triggered as expected")
            elif "成功" in output or "已创建" in output:
                step1["status"] = "ok"
                step1["approval_triggered"] = False
                step1["note"] = "Order created directly (may be below threshold)"
                print("  ~ Order created directly (no approval needed)")
            elif "权限" in output or "拒绝" in output or "无权" in output:
                step1["status"] = "partial"
                step1["approval_triggered"] = False
                step1["note"] = "Employee lacks write permission (security working as intended)"
                print("  ~ Employee blocked by permissions (security correct)")
            else:
                step1["status"] = "partial"
                step1["approval_triggered"] = False
                step1["note"] = f"Unexpected response: {output[:100]}"
                print("  ~ Unexpected response")
        else:
            step1["status"] = "fail"
            step1["error"] = f"HTTP {sse['status_code']}: {sse['error']}"
            print(f"  ✗ Failed: {step1['error']}")
            result["passed"] = False
            result["steps"].append(step1)
            return result
        result["steps"].append(step1)

        # Step 2: Admin approves (if approval was triggered)
        if step1.get("approval_triggered"):
            print("\n[Step 2] Admin reviews pending approvals...")
            resp = await client.get(
                f"{API_BASE}/api/admin/approvals?status=pending",
                headers=admin_headers,
            )
            step2: dict[str, Any] = {"step": 2, "name": "approve"}
            if resp.status_code == 200:
                approvals = resp.json()
                if isinstance(approvals, list) and len(approvals) > 0:
                    approval_id = approvals[0].get("id")
                    resp = await client.post(
                        f"{API_BASE}/api/admin/approvals/{approval_id}/approve",
                        headers=admin_headers,
                        json={"decision": "approved", "notes": "E2E test approval"},
                    )
                    if resp.status_code in (200, 204):
                        step2["status"] = "ok"
                        step2["approval_id"] = approval_id
                        print(f"  ✓ Approval granted: {approval_id}")
                    else:
                        step2["status"] = "fail"
                        step2["error"] = f"HTTP {resp.status_code}"
                        print("  ✗ Approve failed")
                        result["passed"] = False
                else:
                    step2["status"] = "partial"
                    step2["note"] = "No pending approvals found"
                    print("  ~ No pending approvals found")
            else:
                step2["status"] = "fail"
                step2["error"] = f"HTTP {resp.status_code}"
                print("  ✗ List approvals failed")
                result["passed"] = False
            result["steps"].append(step2)

        # Step 3: Verify audit log
        print("\n[Step 3] Verify audit log...")
        await asyncio.sleep(1)
        resp = await client.get(
            f"{API_BASE}/api/admin/audit-logs?limit=10",
            headers=admin_headers,
        )
        step3: dict[str, Any] = {"step": 3, "name": "verify_audit"}
        if resp.status_code == 200:
            body = resp.json()
            # C13/Fix-B3: audit-logs returns {"items": [...], "total": N, ...}
            audit_entries = body.get("items", body) if isinstance(body, dict) else body
            if isinstance(audit_entries, list):
                # Check if any audit entry matches our operation
                write_audits = [
                    a
                    for a in audit_entries
                    if "write" in str(a.get("operation", "")).lower()
                    or "create" in str(a.get("operation", "")).lower()
                    or "write" in str(a.get("action", "")).lower()
                ]
                step3["status"] = "ok" if len(audit_entries) > 0 else "partial"
                step3["audit_count"] = len(audit_entries)
                step3["write_audit_count"] = len(write_audits)
                if len(audit_entries) == 0:
                    step3["note"] = "No audit entries found"
                    print("  ~ No audit entries found")
                else:
                    print(
                        f"  ✓ Audit entries found: {len(audit_entries)} total, "
                        f"{len(write_audits)} writes"
                    )
            else:
                step3["status"] = "partial"
                step3["note"] = "Unexpected audit response format"
                print("  ~ Unexpected audit response")
        else:
            step3["status"] = "partial"
            step3["note"] = f"Audit endpoint returned {resp.status_code}"
            print(f"  ~ Audit endpoint returned {resp.status_code}")
        result["steps"].append(step3)

        # Step 4: Test idempotency (resubmit same request)
        print("\n[Step 4] Test idempotency (resubmit same order)...")
        # C13/Fix-B: Use admin (same as Step 1) for consistency
        sse2 = await invoke_agent_sse(client, admin_token, order_msg)
        step4: dict[str, Any] = {"step": 4, "name": "idempotency"}
        output2 = sse2["final_content"] or ""
        step4["agent_response"] = output2
        if sse2["status_code"] == 200:
            step4["status"] = "ok"
            if "重复" in output2 or "已存在" in output2 or "duplicate" in output2.lower():
                step4["idempotency_verified"] = True
                print("  ✓ Idempotency verified: duplicate detected")
            else:
                step4["idempotency_verified"] = False
                step4["note"] = "Resubmit did not detect duplicate (may be different session)"
                print("  ~ Duplicate not explicitly detected")
        else:
            step4["status"] = "partial"
            step4["error"] = f"HTTP {sse2['status_code']}: {sse2['error']}"
            print(f"  ~ Resubmit returned {sse2['status_code']}")
        result["steps"].append(step4)

    # Summary
    # C13/Fix-6: 仅 "ok" 算 Pass；partial 表示步骤未完全成功（如权限拦截、
    # 幂等未检测、审计未找到写记录），不应判为整体通过。
    all_ok = all(s.get("status") == "ok" for s in result["steps"])
    result["passed"] = all_ok
    print(f"\nDemo B: {'PASS' if result['passed'] else 'FAIL'}")
    return result


# ---------------------------------------------------------------------------
# Strict Demo B: employee request -> different admin approval -> resume ->
# audited write -> idempotent retry -> verified compensating rollback.
# ---------------------------------------------------------------------------


async def demo_b_order_loop() -> dict[str, Any]:
    result: dict[str, Any] = {
        "demo": "B",
        "steps": [],
        "approval_triggered": False,
        "resume_verified": False,
        "separation_of_duties_verified": False,
        "write_audit_linked": False,
        "idempotency_verified": False,
        "rollback_verified": False,
        "passed": False,
    }
    order_msg = (
        "Create a sales order for customer CUS-TECH-0001, product "
        "PRD-ELEC-001, quantity 200, unit price 5000."
    )

    async with httpx.AsyncClient(timeout=300) as client:
        admin_token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        employee_token = await login(client, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Employee submits. PASS requires an actual LangGraph interrupt event.
        requested = await _stream_api_sse(
            client,
            f"{API_BASE}/api/invoke",
            employee_token,
            {"agent_id": DEFAULT_AGENT_ID, "message": order_msg},
        )
        approval_event = _approval_event(requested)
        session_id = requested.get("session_id")
        approval_id = approval_event.get("approval_id") if approval_event else None
        step1_ok = bool(
            requested["status_code"] == 200
            and not requested.get("error")
            and session_id
            and approval_id
        )
        result["approval_triggered"] = step1_ok
        result["steps"].append(
            {
                "step": 1,
                "name": "employee_request_interrupt",
                "status": "ok" if step1_ok else "fail",
                "session_id": session_id,
                "approval_id": approval_id,
                "error": requested.get("error"),
            }
        )
        if not step1_ok:
            return result

        # 2. A different administrator approves the exact pending ticket.
        pending_response = await client.get(
            f"{API_BASE}/api/admin/approvals",
            params={"status": "pending", "limit": 100},
            headers=admin_headers,
        )
        pending_body = pending_response.json() if pending_response.status_code == 200 else {}
        pending_items = pending_body.get("items", []) if isinstance(pending_body, dict) else []
        ticket = next(
            (item for item in pending_items if str(item.get("id")) == str(approval_id)),
            None,
        )
        requested_by = ticket.get("requested_by") if ticket else None
        approve_response = await client.post(
            f"{API_BASE}/api/admin/approvals/{approval_id}/approve",
            headers=admin_headers,
        )
        approved_ok = ticket is not None and approve_response.status_code == 200
        result["steps"].append(
            {
                "step": 2,
                "name": "different_admin_approval",
                "status": "ok" if approved_ok else "fail",
                "requested_by": requested_by,
                "http_status": approve_response.status_code,
            }
        )
        if not approved_ok:
            return result

        # 3. The requester resumes the durable graph; the persisted intent is
        # reconstructed server-side and must produce a linked successful audit.
        resumed = await _stream_api_sse(
            client,
            f"{API_BASE}/api/interrupt/{session_id}/resume",
            employee_token,
            {
                "agent_id": DEFAULT_AGENT_ID,
                "approval_id": approval_id,
                "decision": "approved",
            },
        )
        outcome = _write_outcome(resumed) or {}
        audit_id = outcome.get("audit_id")
        resume_ok = bool(
            resumed["status_code"] == 200
            and not resumed.get("error")
            and outcome.get("success") is True
            and str(outcome.get("approval_id")) == str(approval_id)
            and audit_id
        )
        result["resume_verified"] = resume_ok
        result["steps"].append(
            {
                "step": 3,
                "name": "resume_and_write",
                "status": "ok" if resume_ok else "fail",
                "audit_id": audit_id,
                "outcome": outcome,
                "error": resumed.get("error"),
            }
        )
        if not resume_ok:
            return result

        audit_response = await client.get(
            f"{API_BASE}/api/admin/write-audits/{audit_id}",
            headers=admin_headers,
        )
        audit = audit_response.json() if audit_response.status_code == 200 else {}
        trace_response = await client.get(
            f"{API_BASE}/api/admin/spans/trace/{audit.get('trace_id')}",
            headers=admin_headers,
        )
        linked_spans = trace_response.json() if trace_response.status_code == 200 else []
        audit_linked = bool(
            audit_response.status_code == 200
            and audit.get("success") is True
            and str(audit.get("approval_id")) == str(approval_id)
            and str(audit.get("session_id")) == str(session_id)
            and audit.get("trace_id")
            and isinstance(linked_spans, list)
            and len(linked_spans) > 0
            and audit.get("idempotency_key")
            and isinstance(audit.get("after_state"), dict)
            and audit["after_state"].get("id")
        )
        result["write_audit_linked"] = audit_linked
        result["steps"].append(
            {
                "step": 4,
                "name": "linked_write_audit",
                "status": "ok" if audit_linked else "fail",
                "audit": audit,
                "linked_span_count": len(linked_spans),
            }
        )
        if not audit_linked:
            return result

        # Fetch the decided ticket to prove requester and approver are different.
        approvals_response = await client.get(
            f"{API_BASE}/api/admin/approvals",
            params={"limit": 100},
            headers=admin_headers,
        )
        approvals_body = approvals_response.json() if approvals_response.status_code == 200 else {}
        approval_items = approvals_body.get("items", []) if isinstance(approvals_body, dict) else []
        decided_ticket = next(
            (item for item in approval_items if str(item.get("id")) == str(approval_id)),
            None,
        )
        decided_by = decided_ticket.get("decided_by") if decided_ticket else None
        separated = bool(requested_by and decided_by and str(requested_by) != str(decided_by))
        result["separation_of_duties_verified"] = separated
        result["steps"].append(
            {
                "step": 5,
                "name": "separation_of_duties",
                "status": "ok" if separated else "fail",
                "requested_by": requested_by,
                "decided_by": decided_by,
            }
        )
        if not separated:
            return result

        # 5. Same request + same session must return the original audit rather
        # than create another approval/write, including after a process restart.
        retried = await _stream_api_sse(
            client,
            f"{API_BASE}/api/invoke",
            employee_token,
            {
                "agent_id": DEFAULT_AGENT_ID,
                "session_id": session_id,
                "message": order_msg,
            },
        )
        retry_outcome = _write_outcome(retried) or {}
        idempotent = bool(
            retried["status_code"] == 200
            and _approval_event(retried) is None
            and retry_outcome.get("success") is True
            and str(retry_outcome.get("audit_id")) == str(audit_id)
        )
        result["idempotency_verified"] = idempotent
        result["steps"].append(
            {
                "step": 6,
                "name": "idempotent_retry",
                "status": "ok" if idempotent else "fail",
                "outcome": retry_outcome,
                "error": retried.get("error"),
            }
        )
        if not idempotent:
            return result

        # 6. Compensate the successful create and require verified audit state.
        rollback_response = await client.post(
            f"{API_BASE}/api/admin/write-audits/{audit_id}/rollback",
            headers=admin_headers,
            json={"reason": "competition Demo B verified compensation"},
        )
        rollback_body = rollback_response.json() if rollback_response.status_code == 200 else {}
        final_audit_response = await client.get(
            f"{API_BASE}/api/admin/write-audits/{audit_id}",
            headers=admin_headers,
        )
        final_audit = final_audit_response.json() if final_audit_response.status_code == 200 else {}
        rollback_ok = bool(
            rollback_response.status_code == 200
            and rollback_body.get("success") is True
            and rollback_body.get("rolled_back") is True
            and final_audit.get("rolled_back") is True
            and final_audit.get("rollback_reason")
        )
        result["rollback_verified"] = rollback_ok
        result["steps"].append(
            {
                "step": 7,
                "name": "verified_compensating_rollback",
                "status": "ok" if rollback_ok else "fail",
                "rollback": rollback_body,
                "final_audit": final_audit,
            }
        )

    required = (
        "approval_triggered",
        "resume_verified",
        "separation_of_duties_verified",
        "write_audit_linked",
        "idempotency_verified",
        "rollback_verified",
    )
    result["passed"] = all(result[key] is True for key in required)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(demo: str = "both", run_id: str | None = None) -> int:
    run_id = run_id or f"e2e-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, digits, '.', '_' and '-'")
    results_dir = PROJECT_ROOT / "benchmarks" / "competition" / "results" / run_id
    results_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC)

    print("=" * 60)
    print("C11: Two Main Demo E2E Tests")
    print(f"  API: {API_BASE}")
    print("=" * 60)

    results: dict[str, Any] = {}

    if demo in ("A", "both"):
        results["demo_a"] = await demo_a_knowledge_loop()

    if demo in ("B", "both"):
        results["demo_b"] = await demo_b_order_loop()

    # Save raw demo outcomes and a separately hashed run manifest.
    results_file = results_dir / "e2e_results.json"
    results_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    finished_at = datetime.now(UTC)

    def collect_ids(field: str) -> list[str]:
        values = {
            str(demo_result[field])
            for demo_result in results.values()
            if demo_result.get(field)
        }
        for demo_result in results.values():
            for step in demo_result.get("steps", []):
                if step.get(field):
                    values.add(str(step[field]))
        return sorted(values)

    source_status = _git_output("status", "--porcelain", "--untracked-files=no")
    run_manifest = {
        "schema_version": "e2e-v2",
        "run_id": run_id,
        "suite": demo,
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "git_sha": _git_output("rev-parse", "HEAD") or None,
        "tracked_source_dirty": bool(source_status),
        "model": os.environ.get("EAOS_LLM__DEFAULT_MODEL"),
        "embedding_model": os.environ.get("EAOS_EMBEDDING__MODEL"),
        "session_ids": collect_ids("session_id"),
        "contribution_ids": collect_ids("contribution_id"),
        "document_ids": collect_ids("document_id"),
        "approval_ids": collect_ids("approval_id"),
        "audit_ids": collect_ids("audit_id"),
        "all_passed": all(item.get("passed") is True for item in results.values()),
        "artifacts": {
            "e2e_results.json": {
                "sha256": _sha256_file(results_file),
                "bytes": results_file.stat().st_size,
            }
        },
    }
    manifest_file = results_dir / "run_manifest.json"
    manifest_file.write_text(
        json.dumps(run_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nResults saved: {results_file}")

    # Summary
    print("\n" + "=" * 60)
    print("Summary:")
    for demo_key, demo_result in results.items():
        status = "PASS" if demo_result.get("passed") else "FAIL"
        print(f"  {demo_key}: {status}")
    print("=" * 60)

    return 0 if all(r.get("passed") for r in results.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run E2E demo tests")
    parser.add_argument("--demo", choices=["A", "B", "both"], default="both")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.demo, args.run_id)))
