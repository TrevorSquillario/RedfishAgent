from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

@dataclass
class LogEntry:
    source: str
    payload: Dict[str, Any]
    labels: Dict[str, Any]

#
# Config Models
#   
@dataclass
class PluginsConfig:
    inventory: List[str] = field(default_factory=list)
    output: List[str] = field(default_factory=list)
    trigger: List[str] = field(default_factory=list)

@dataclass
class ConfigModel:
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    mcp_servers: List[str] = field(default_factory=list)
    prompts: Dict[str, Any] = field(default_factory=dict)
