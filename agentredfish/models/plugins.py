from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class InventoryEntry(BaseModel):
	"""Represents a single Prometheus HTTP Service Discovery entry.

	- `targets`: list of target URLs/addresses (usually formatted by the YAML plugin)
	- `labels`: optional map of label name -> value

	Based on the Prometheus Service Discovery format
	https://docs.victoriametrics.com/victoriametrics/sd_configs/#http_sd_configs

	[
		{
			"targets": [ "<host>", ... ],
			"labels": {
			"<labelname>": "<labelvalue>",
			...
			}
		},
		...
	]
	"""

	targets: List[str] = Field(..., min_items=1)
	labels: Optional[Dict[str, str]] = None


class Inventory(BaseModel):
	"""Top-level inventory model containing a list of entries.

	Use `Inventory.parse_obj()` or `Inventory.from_list()` to construct from
	the YAML-normalized list of dicts produced by the inventory plugin.
	"""

	entries: List[InventoryEntry]

	@classmethod
	def from_list(cls, data: List[dict]) -> "Inventory":
		return cls(entries=[InventoryEntry.parse_obj(d) for d in data])


__all__ = ["InventoryEntry", "Inventory"]

