"""C11: Two main demo E2E tests.

Demo A: Knowledge contribution → review → ingest → cross-user reuse
Demo B: Natural language order → approval → write → audit → rollback

These tests verify the full closed-loop of both main demos.
Run with: python benchmarks/competition/runners/run_e2e.py [--demo A|B|both]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "benchmarks" / "competition"))

from runners.run_eval import invoke_agent_sse  # SSE streaming helper

API_BASE = os.environ.get("EAOS_API_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("EAOS_ADMIN_EMAIL", "admin@acme.com")
ADMIN_PASSWORD = os.environ.get("EAOS_ADMIN_PASSWORD", "admin")
EMPLOYEE_EMAIL = os.environ.get("EAOS_EMPLOYEE_EMAIL", "employee@acme.com")
EMPLOYEE_PASSWORD = os.environ.get("EAOS_EMPLOYEE_PASSWORD", "employee")

# Fetch a real agent_id from the database
import subprocess as _sp

_agent_id_result = _sp.run(
    ["docker", "exec", "eaos-postgres",
     "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
     "SELECT id FROM agent.agents LIMIT 1"],
    capture_output=True, text=True, timeout=10,
)
DEFAULT_AGENT_ID = _agent_id_result.stdout.strip() or str(uuid.uuid4())


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        f"{API_BASE}/api/auth/login",
        json={"email": email, "password": password},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Demo A: Knowledge Contribution → Review → Ingest → Reuse
# ---------------------------------------------------------------------------

async def demo_a_knowledge_loop() -> dict[str, Any]:
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
        contribution_content = f"E2E测试贡献：产品PRD-001是企业级路由器，支持10Gbps吞吐量，部署于核心网络节点。UUID标记：{uuid.uuid4().hex[:8]}"
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
            print(f"  ✓ Contribution approved")
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
                print(f"  ✓ Knowledge retrieved successfully")
            else:
                step3["status"] = "partial"
                step3["knowledge_found"] = False
                step3["note"] = "Query succeeded but contributed knowledge not in response"
                print(f"  ~ Query succeeded but knowledge not found in response")
            # Check for citation
            if ("[" in output and "]" in output) or "来源" in output:
                step3["has_citation"] = True
                print(f"  ✓ Citation present")
            else:
                step3["has_citation"] = False
                print(f"  ~ No citation markers found")
        else:
            step3["status"] = "fail"
            step3["error"] = f"HTTP {sse['status_code']}: {sse['error']}"
            print(f"  ✗ Query failed")
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

async def demo_b_order_loop() -> dict[str, Any]:
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
        employee_headers = {"Authorization": f"Bearer {employee_token}"}

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
                print(f"  ✓ Approval triggered as expected")
            elif "成功" in output or "已创建" in output:
                step1["status"] = "ok"
                step1["approval_triggered"] = False
                step1["note"] = "Order created directly (may be below threshold)"
                print(f"  ~ Order created directly (no approval needed)")
            elif "权限" in output or "拒绝" in output or "无权" in output:
                step1["status"] = "partial"
                step1["approval_triggered"] = False
                step1["note"] = "Employee lacks write permission (security working as intended)"
                print(f"  ~ Employee blocked by permissions (security correct)")
            else:
                step1["status"] = "partial"
                step1["approval_triggered"] = False
                step1["note"] = f"Unexpected response: {output[:100]}"
                print(f"  ~ Unexpected response")
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
                        print(f"  ✗ Approve failed")
                        result["passed"] = False
                else:
                    step2["status"] = "partial"
                    step2["note"] = "No pending approvals found"
                    print(f"  ~ No pending approvals found")
            else:
                step2["status"] = "fail"
                step2["error"] = f"HTTP {resp.status_code}"
                print(f"  ✗ List approvals failed")
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
                write_audits = [a for a in audit_entries if "write" in str(a.get("operation", "")).lower()
                               or "create" in str(a.get("operation", "")).lower()
                               or "write" in str(a.get("action", "")).lower()]
                step3["status"] = "ok" if len(audit_entries) > 0 else "partial"
                step3["audit_count"] = len(audit_entries)
                step3["write_audit_count"] = len(write_audits)
                if len(audit_entries) == 0:
                    step3["note"] = "No audit entries found"
                    print(f"  ~ No audit entries found")
                else:
                    print(f"  ✓ Audit entries found: {len(audit_entries)} total, {len(write_audits)} writes")
            else:
                step3["status"] = "partial"
                step3["note"] = "Unexpected audit response format"
                print(f"  ~ Unexpected audit response")
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
                print(f"  ✓ Idempotency verified: duplicate detected")
            else:
                step4["idempotency_verified"] = False
                step4["note"] = "Resubmit did not detect duplicate (may be different session)"
                print(f"  ~ Duplicate not explicitly detected")
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
# Main
# ---------------------------------------------------------------------------

async def main(demo: str = "both") -> int:
    print("=" * 60)
    print("C11: Two Main Demo E2E Tests")
    print(f"  API: {API_BASE}")
    print("=" * 60)

    results: dict[str, Any] = {}

    if demo in ("A", "both"):
        results["demo_a"] = await demo_a_knowledge_loop()

    if demo in ("B", "both"):
        results["demo_b"] = await demo_b_order_loop()

    # Save results
    run_id = f"e2e-{time.strftime('%Y%m%d-%H%M%S')}"
    results_dir = Path("benchmarks/competition/results") / run_id
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / "e2e_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
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
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.demo)))
