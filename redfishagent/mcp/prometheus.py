import json
import logging
import textwrap
from typing import List, Optional

import requests
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("victoria_metrics_mcp")

mcp = FastMCP("VictoriaMetrics MCP")


class RedfishMetricTool:
    def __init__(self, url: str = "http://victoria-metrics:8428"):
        self.api_url = f"{url.rstrip('/')}/api/v1"

    def discover_metrics(self, service_tag: str) -> List[str]:
        """
        Returns a list of all metric names currently being recorded for a specific Service Tag.
        """
        params = {"match[]": f'{{service_tag="{service_tag}"}}'}
        resp = requests.get(f"{self.api_url}/label/__name__/values", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_metric_analysis(self, service_tag: str, metric_name: str, window_hours: int = 3) -> str:
        """
        Fetches the last X hours of data and returns a statistical summary.
        Calculates: Current Value, Average, Max, and Z-Score (Anomaly score).
        """
        query = textwrap.dedent(
            f"""
            label_replace(
              last_over_time({metric_name}{{service_tag="{service_tag}"}}[1m]), "stat", "current", "", ""
            ) or
            label_replace(
              avg_over_time({metric_name}{{service_tag="{service_tag}"}}[{window_hours}h]), "stat", "average", "", ""
            ) or
            label_replace(
              (last_over_time({metric_name}{{service_tag="{service_tag}"}}[1m]) - 
               avg_over_time({metric_name}{{service_tag="{service_tag}"}}[{window_hours}h])) / 
               stddev_over_time({metric_name}{{service_tag="{service_tag}"}}[{window_hours}h]), "stat", "z_score", "", ""
            )
            """
        ).strip()
        resp = requests.get(f"{self.api_url}/query", params={"query": query}, timeout=30)
        resp.raise_for_status()
        return self._format_stats(resp.json())

    def _format_stats(self, data: dict) -> str:
        try:
            results = data.get("data", {}).get("result", [])
            stats = {}
            for res in results:
                stat_name = res.get("metric", {}).get("stat")
                value = res.get("value", [None, None])[1]
                stats[stat_name] = value
            return f"Stats: Current={stats.get('current')}, Avg={stats.get('average')}, Z-Score={stats.get('z_score')}"
        except Exception:
            logger.exception("error formatting stats")
            return "Stats: unavailable"


# FastMCP tool wrappers


@mcp.tool
def discover_metrics(service_tag: str, vm_url: Optional[str] = None) -> str:
    url = vm_url or "http://victoria-metrics:8428"
    tool = RedfishMetricTool(url=url)
    names = tool.discover_metrics(service_tag)
    return json.dumps(names)


@mcp.tool
def get_metric_analysis(service_tag: str, metric_name: str, window_hours: int = 3, vm_url: Optional[str] = None) -> str:
    url = vm_url or "http://victoria-metrics:8428"
    tool = RedfishMetricTool(url=url)
    analysis = tool.get_metric_analysis(service_tag, metric_name, window_hours)
    return json.dumps({"analysis": analysis})


@mcp.tool
def health_check() -> str:
    return json.dumps({"status": "ok"})


if __name__ == "__main__":
    # Run over stdio for FastMCP transport
    mcp.run(transport="stdio")
