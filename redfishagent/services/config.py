from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import os

import yaml


@dataclass
class PluginsConfig:
    inventory: List[str] = field(default_factory=list)
    output: List[str] = field(default_factory=list)


@dataclass
class ConfigModel:
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    mcp: List[str] = field(default_factory=list)


class ConfigService:
    """Loads the repository `/config/config.yaml` and exposes parsed config.

    The service only supports the single canonical path at the repo root:
    `/config/config.yaml`. The YAML is parsed during initialization and
    available as the `config` attribute (a `ConfigModel`).
    """

    def __init__(self):
        # Allow overriding the config path via the CONFIG_PATH env var
        config_path = os.getenv("CONFIG_PATH", "/config/config.yaml")
        self.path = Path(config_path)
        self.config = self._load()

    def _load(self) -> ConfigModel:
        with self.path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        plugins = data.get("plugins", {}) or {}
        inventory = plugins.get("inventory") or []
        output = plugins.get("output") or []
        mcp = data.get("mcp") or []

        return ConfigModel(
            plugins=PluginsConfig(inventory=list(inventory), output=list(output)),
            mcp=list(mcp),
        )
