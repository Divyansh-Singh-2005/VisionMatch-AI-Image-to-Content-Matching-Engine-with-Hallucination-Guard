from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr = SecretStr("")
    vision_model: str = "gemini-3.1-flash-lite"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    database_url: str = "postgresql+psycopg://imgmatch:imgmatch@localhost:5434/imgmatch"

    similarity_threshold: float = 0.70
    min_vision_confidence: float = 0.60
    top_k: int = 5

    daily_call_budget: int = 500
    vision_rpm_limit: int = 8
    embed_rpm_limit: int = 60
    max_attempts: int = 3

    images_dir: str = "data/images"
    posts_file: str = "data/posts/posts.json"
    default_tenant_id: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()