from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

@dataclass
class LogEntry:
    source: str
    labels: Dict[str, Any]
    event_id: str
    event_timestamp: str
    event_type: Optional[str] = None
    message: Optional[str] = None
    message_id: Optional[str] = None
    severity: Optional[str] = None

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
