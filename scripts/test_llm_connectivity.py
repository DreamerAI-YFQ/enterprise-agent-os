"""Quick connectivity test for LLM + Embedding via DashScope.

Run: uv run python scripts/test_llm_connectivity.py

Tests:
1. Chat completion (qwen3-omni-flash)
2. Streaming chat
3. Embedding (text-embedding-v3)

No database or Docker required — just API connectivity.
"""

import asyncio
import os

# Load .env manually (avoid pulling full AppConfig which needs DB)
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


async def test_chat() -> None:
    from eaos.core.config import LLMConfig
    from eaos.infra.llm.base import Message
    from eaos.infra.llm.openai_adapter import OpenAILLMClient

    cfg = LLMConfig()
    base_url = cfg.openai_base_url
    api_key = cfg.openai_api_key
    model = cfg.default_model

    print(f"\n[1/3] Chat test — model={model}, base_url={base_url}")
    print(f"      API key: {api_key[:8]}...{api_key[-4:] if api_key else '(none)'}")

    if not api_key or api_key == "sk-placeholder":
        print("      SKIP: API key not set (still placeholder)")
        return

    client = OpenAILLMClient(cfg, base_url=base_url)
    messages = [
        Message(role="system", content="你是一个简洁的助手。"),
        Message(role="user", content="说一句话证明你能正常工作。"),
    ]
    resp = await client.chat(messages, model=model, temperature=0.3)
    print(f"      Response: {resp.content}")
    print(f"      Tokens: prompt={resp.prompt_tokens}, completion={resp.completion_tokens}")
    print("      PASS: Chat works!")


async def test_stream() -> None:
    from eaos.core.config import LLMConfig
    from eaos.infra.llm.base import Message
    from eaos.infra.llm.openai_adapter import OpenAILLMClient

    cfg = LLMConfig()
    api_key = cfg.openai_api_key
    model = cfg.default_model
    base_url = cfg.openai_base_url

    print(f"\n[2/3] Stream test — model={model}")

    if not api_key or api_key == "sk-placeholder":
        print("      SKIP: API key not set")
        return

    client = OpenAILLMClient(cfg, base_url=base_url)
    messages = [Message(role="user", content="从1数到5，用中文")]
    tokens: list[str] = []
    async for token in client.stream(messages, model=model, temperature=0.3):
        tokens.append(token)
    full = "".join(tokens)
    print(f"      Streamed: {full}")
    print(f"      Chunks: {len(tokens)}")
    print("      PASS: Stream works!")


async def test_embedding() -> None:
    from eaos.core.config import EmbeddingConfig
    from eaos.infra.vector.embedder import OpenAIEmbedder

    cfg = EmbeddingConfig()
    api_key = cfg.api_key
    model = cfg.model
    base_url = cfg.base_url
    dims = cfg.dimensions

    print(f"\n[3/3] Embedding test — model={model}, dims={dims}")
    print(f"      base_url={base_url}")

    if not api_key or api_key == "sk-placeholder":
        print("      SKIP: API key not set")
        return

    embedder = OpenAIEmbedder(cfg)
    vec = await embedder.embed("企业智能体操作系统测试")
    print(f"      Vector length: {len(vec)}")
    print(f"      First 5 values: {vec[:5]}")
    if len(vec) == dims:
        print(f"      PASS: Embedding works! Dimension matches ({dims})")
    else:
        print(f"      WARN: Dimension mismatch — expected {dims}, got {len(vec)}")


async def main() -> None:
    print("=" * 60)
    print("EAOS LLM Connectivity Test (DashScope / 阿里云百炼)")
    print("=" * 60)

    try:
        await test_chat()
    except Exception as e:
        print(f"      FAIL: {e}")

    try:
        await test_stream()
    except Exception as e:
        print(f"      FAIL: {e}")

    try:
        await test_embedding()
    except Exception as e:
        print(f"      FAIL: {e}")

    print("\n" + "=" * 60)
    print("Done. Replace sk-placeholder in .env with your real DashScope API key.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
