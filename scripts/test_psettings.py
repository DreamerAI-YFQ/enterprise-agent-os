from pydantic_settings import BaseSettings, SettingsConfigDict


class C(BaseSettings):
    api_key: str | None = None
    base_url: str | None = None
    model_config = SettingsConfigDict(
        env_prefix="EAOS_EMBEDDING__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


c = C()
print(f"api_key set: {bool(c.api_key)}")
print(f"base_url: {c.base_url}")
print(f"model: {c.model if hasattr(c, 'model') else 'N/A'}")
