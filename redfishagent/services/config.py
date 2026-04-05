from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
import os

import yaml

from models.app import ConfigModel, PluginsConfig

class ConfigService:
    """Loads the repository `/config/config.yaml` and exposes parsed config.

    The service only supports the single canonical path at the repo root:
    `/config/config.yaml`. The YAML is parsed during initialization and
    available as the `config` attribute (a `ConfigModel`).

    The constructor accepts an optional `config_path`. If omitted or `None`,
    the path is taken from the `CONFIG_PATH` environment variable or the
    default `/config/config.yaml`.
    """

    def __init__(self, config_path: Optional[str] = None):
        # Allow overriding the config path via the CONFIG_PATH env var
        if config_path:
            self.path = Path(config_path)
        else:
            self.path = Path(os.getenv("CONFIG_PATH", "/config/config.yaml"))
        self.config = self._load()

    def _load(self) -> ConfigModel:
        with self.path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        plugins = data.get("plugins", {}) or {}
        inventory = plugins.get("inventory") or []
        output = plugins.get("output") or []
        mcp_servers = data.get("mcp_servers") or []
        prompts = data.get("prompts") or {}

        return ConfigModel(
            plugins=PluginsConfig(inventory=list(inventory), output=list(output)),
            mcp_servers=list(mcp_servers),
            prompts=dict(prompts)
        )
