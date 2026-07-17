import os

from dotenv import load_dotenv
from eaos.core.config import AppConfig

print("before load_dotenv:")
print("  EMB:", repr(os.getenv("EAOS_EMBEDDING__API_KEY")))

r = load_dotenv(".env", override=True)
print(f"load_dotenv returned: {r}")

print("after load_dotenv:")
print("  EMB:", repr(os.getenv("EAOS_EMBEDDING__API_KEY")))

cfg = AppConfig()
print(f"AppConfig embedding api_key set: {bool(cfg.embedding.api_key)}")
print(f"AppConfig llm openai_api_key set: {bool(cfg.llm.openai_api_key)}")
