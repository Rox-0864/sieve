"""
Configuracion central. Se lee del entorno UNA vez, al importar.

Patron: settings object, no `os.getenv()` esparcido por el codigo.
Razon: si settings se lee en 40 lugares distintos, cambiar un valor es
un juego de adivinas y el error aparece tres modulos mas alla.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── LLM ───────────────────────────────────────────────────
    llm_provider: str = Field(default="gemini")
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    llm_analysis_model: str = ""
    llm_writer_model: str = ""
    llm_analysis_temperature: float = 0.0
    llm_writer_temperature: float = 0.7

    # ─── OCI ───────────────────────────────────────────────────
    oci_config_path: str = "~/.oci/config"
    oci_profile: str = "DEFAULT"
    oci_compartment_id: str = ""
    oci_bucket: str = "communitylab-activos-marketing"
    oci_prefix: str = "activos"

    # ─── App ───────────────────────────────────────────────────
    max_batch_size: int = 100
    streamlit_port: int = 8501
    app_env: str = "local"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
