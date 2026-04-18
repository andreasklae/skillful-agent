"""Server configuration, Azure Key Vault integration, and CORS setup."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Shared skills dir: frontend/mimir-agent/app/skills/ relative to repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SKILLS_DIR = str(_REPO_ROOT / "frontend" / "mimir-agent" / "app" / "skills")

logger = logging.getLogger(__name__)


@dataclass
class ServerSettings:
    """Server configuration loaded from environment variables."""

    keyvault_name: str = ""
    cors_allow_origins: str = "*"
    skills_dir: str = _DEFAULT_SKILLS_DIR
    openai_model: str = "gpt-5.4-mini"
    openai_api_key: str | None = None
    # Azure OpenAI — if azure_endpoint is set, AzureProvider is used instead of OpenAIProvider
    azure_endpoint: str | None = None
    azure_api_key: str | None = None
    azure_api_version: str = "2024-07-01-preview"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServerSettings:
        return cls(
            keyvault_name=os.getenv("SKILL_AGENT_KEYVAULT_NAME", "easyflex"),
            cors_allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*"),
            skills_dir=os.getenv("SKILL_AGENT_SKILLS_DIR", _DEFAULT_SKILLS_DIR),
            openai_model=os.getenv("SKILL_AGENT_OPENAI_MODEL", "gpt-5.4-mini"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            azure_endpoint=os.getenv("SKILL_AGENT_AZURE_ENDPOINT"),
            azure_api_key=os.getenv("SKILL_AGENT_AZURE_API_KEY"),
            azure_api_version=os.getenv("SKILL_AGENT_AZURE_API_VERSION", "2024-07-01-preview"),
            log_level=os.getenv("SKILL_AGENT_LOG_LEVEL", "INFO"),
        )

    @property
    def use_azure(self) -> bool:
        return bool(self.azure_endpoint)

    def parsed_cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins, defaulting to ["*"]."""
        origins = [o.strip() for o in self.cors_allow_origins.split(",")]
        return [o for o in origins if o] or ["*"]


def configure_logging(settings: ServerSettings) -> None:
    """Set up root logging based on settings."""
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def get_keyvault_secrets(keyvault_name: str) -> dict[str, str]:
    """Fetch all enabled secrets from an Azure Key Vault as a flat dict."""
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    vault_url = f"https://{keyvault_name}.vault.azure.net"
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    secrets: dict[str, str] = {}
    for prop in client.list_properties_of_secrets():
        if prop.enabled:
            secret = client.get_secret(prop.name)
            secrets[prop.name] = secret.value
    return secrets


def resolve_openai_api_key(settings: ServerSettings) -> str:
    """Return the OpenAI API key, fetching from Key Vault if not set directly."""
    if settings.openai_api_key:
        return settings.openai_api_key
    if not settings.keyvault_name:
        raise RuntimeError(
            "No OPENAI_API_KEY set and no keyvault_name configured. "
            "Set OPENAI_API_KEY or SKILL_AGENT_KEYVAULT_NAME."
        )
    secrets = get_keyvault_secrets(settings.keyvault_name)
    api_key = secrets.get("OPENAI-API-KEY")
    if not api_key:
        raise RuntimeError(
            f"OPENAI-API-KEY not found in Key Vault '{settings.keyvault_name}'."
        )
    return api_key
