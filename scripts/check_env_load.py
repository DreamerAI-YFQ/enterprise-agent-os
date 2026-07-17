"""Check what AppConfig.load_config() sees without pre-loading env."""

import os
from pathlib import Path

print(f"cwd: {os.getcwd()}")
print(f".env exists: {(Path.cwd() / '.env').exists()}")

from eaos.core.config import AppConfig  # noqa: E402

cfg = AppConfig.load_config()
print(f"embedding.api_key set: {bool(cfg.embedding.api_key)}")
embedding_key_prefix = cfg.embedding.api_key[:20] if cfg.embedding.api_key else "None"
print(f"embedding.api_key prefix: {embedding_key_prefix}...")
print(f"embedding.base_url: {cfg.embedding.base_url}")
print(f"embedding.model: {cfg.embedding.model}")
print(f"llm.openai_api_key set: {bool(cfg.llm.openai_api_key)}")
print(f"llm.default_model: {cfg.llm.default_model}")
