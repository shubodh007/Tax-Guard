"""Centralized, typed configuration using the standard library only.

No secrets are hardcoded. Values come from the environment with safe
defaults suitable for local development and tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_title: str = "TaxGuard"
    app_version: str = "0.1.0"
    environment: str = "development"
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")


def _cors_origins(environment: str) -> tuple[str, ...]:
    raw = os.getenv("TAXGUARD_CORS_ORIGINS", "")
    origins = tuple(o.strip() for o in raw.split(",") if o.strip())
    if origins:
        for origin in origins:
            if origin == "*" or not (
                origin.startswith("http://") or origin.startswith("https://")
            ):
                raise ValueError(
                    "TAXGUARD_CORS_ORIGINS must be comma-separated "
                    "http(s) origins without wildcards"
                )
        return origins
    if environment == "production":
        return ()
    return Settings.cors_origins


def get_settings() -> Settings:
    """Build settings from the environment. No I/O, no network, no secrets."""
    environment = os.getenv("TAXGUARD_ENVIRONMENT", Settings.environment)
    return Settings(
        app_title=os.getenv("TAXGUARD_APP_TITLE", Settings.app_title),
        app_version=os.getenv("TAXGUARD_APP_VERSION", Settings.app_version),
        environment=environment,
        cors_origins=_cors_origins(environment),
    )


settings = get_settings()
