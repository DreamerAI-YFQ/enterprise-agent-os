"""Quick API smoke test."""

import subprocess

import httpx

BASE = "http://localhost:8000"

# Get a real agent_id from the database
result = subprocess.run(
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
agent_id = result.stdout.strip()
if not agent_id:
    print("No agents found in database!")
    exit(1)
print(f"Using agent_id: {agent_id}")

# Login
r = httpx.post(
    f"{BASE}/api/auth/login",
    json={
        "tenant_slug": "acme-corp",
        "email": "employee@acme.com",
        "password": "EaosDemo-Employee-2026!",
    },
)
print(f"Login status: {r.status_code}")

if r.status_code != 200:
    print(f"Login failed: {r.text}")
    exit(1)

data = r.json()
token = data.get("access_token") or data.get("token")

# Test invoke (SSE stream)
with httpx.stream(
    "POST",
    f"{BASE}/api/invoke",
    headers={"Authorization": f"Bearer {token}"},
    json={"message": "你好", "agent_id": agent_id},
    timeout=60,
) as r2:
    print(f"Invoke status: {r2.status_code}")
    text = ""
    for line in r2.iter_lines():
        text += line + "\n"
        if len(text) > 800:
            break
    print(text[:800])
