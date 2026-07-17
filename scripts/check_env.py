"""Check what AppConfig sees for embedding and LLM."""

import os
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

from eaos.core.config import AppConfig  # noqa: E402

cfg = AppConfig.load_config(env_file=None)
print(f"EAOS_EMBEDDING__API_KEY env: {os.getenv('EAOS_EMBEDDING__API_KEY', 'NOT SET')[:20]}...")
embedding_key_prefix = cfg.embedding.api_key[:20] if cfg.embedding.api_key else "None"
print(f"AppConfig.embedding.api_key:  {embedding_key_prefix}...")
print(f"AppConfig.embedding.base_url: {cfg.embedding.base_url}")
print(f"AppConfig.embedding.model:    {cfg.embedding.model}")
llm_key_prefix = cfg.llm.openai_api_key[:20] if cfg.llm.openai_api_key else "None"
print(f"AppConfig.llm.openai_api_key: {llm_key_prefix}...")
print(f"AppConfig.llm.default_model:  {cfg.llm.default_model}")
