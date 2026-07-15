# EAOS Scripts

运维与发布验证脚本。

## happy_path.py

端到端 happy path 验证：health → /invoke → evolution run → status 轮询。

### 前置条件

完整栈已启动并初始化：

```bash
# 方式一：本地运行
make up
make migrate
make seed
make serve   # 或: uv run uvicorn eaos_api.main:app --host 0.0.0.0 --port 8000

# 方式二：Docker
docker compose up -d
docker compose run --rm migrate
docker compose exec api uv run python -m eaos.infra.db.seed
```

### 运行

```bash
# 确保 EAOS_SECRET_KEY 与 API 服务端一致
export EAOS_SECRET_KEY="your-secret"
uv run python scripts/happy_path.py
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EAOS_API_URL` | `http://localhost:8000` | API 服务地址 |
| `EAOS_SECRET_KEY` | `test-secret` | JWT 签名密钥（需与服务端一致） |
| `EAOS_LLM__DEFAULT_MODEL` | `gpt-4o-mini` | evolution run 的 base_model |

### 输出

脚本逐步打印验证结果，成功时退出码 0，失败时退出码 1。

```
EAOS Happy Path — targeting http://localhost:8000

1. Health check...
  [OK] /health → {'status': 'ok'}
2. POST /invoke (SSE stream)...
  [OK] received 3 events, final event present
3. POST /admin/evolution/run...
  [OK] evolution run started: ...
4. Polling /admin/evolution/status...
  initial stage: feedback
  [OK] evolution reached terminal stage: blocked

Happy path completed successfully.
```

### 说明

- 走 HTTP（验证 API 层），不走内部函数调用
- SSE 流式接收 `/invoke` 响应
- 不断言 evolution 必须到 `full`（可能因 LLM 质量指标 blocked，只要状态机推进到终态即通过）
- 终态：`full` / `blocked` / `rejected` / `approved`
