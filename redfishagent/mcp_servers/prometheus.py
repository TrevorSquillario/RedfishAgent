import json
import logging
import textwrap
from typing import List, Optional

import asyncio
import aiohttp
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prometheus-mcp")

mcp_server = FastMCP("VictoriaMetrics MCP")


class RedfishMetricTool:
    def __init__(self, url: str = "http://localhost:8428"):
        self.api_url = f"{url.rstrip('/')}/api/v1"

    async def discover_metrics(self, host: str) -> List[str]:
        """
        Returns a list of all metric names currently being recorded for a specific host.
        """
        params = {"match[]": f'{{instance="{host}"}}'}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.api_url}/label/__name__/values", params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                logger.info(f"Metrics for host {host}: {data}")
                return data.get("data", [])

    async def get_metric_analysis(self, host: str, metric_name: str, window_hours: int = 3) -> str:
        """
        Fetches the last X hours of data and returns a statistical summary.
        Calculates: Current Value, Average, and Z-Score (Anomaly score) for a given host.
        """
        # Build three separate queries: current, average, and z_score
        current_q = (
            f'label_replace(last_over_time({metric_name}{{instance="{host}"}}[1m]), "stat", "current", "", "")'
        )
        avg_q = (
            f'label_replace(avg_over_time({metric_name}{{instance="{host}"}}[{window_hours}h]), "stat", "average", "", "")'
        )
        z_q = textwrap.dedent(
            f"""
            label_replace(
                (
                    last_over_time({metric_name}{{instance="{host}"}}[1m]) -
                    avg_over_time({metric_name}{{instance="{host}"}}[{window_hours}h])
                ) / stddev_over_time({metric_name}{{instance="{host}"}}[{window_hours}h]),
                "stat", "z_score", "", ""
            )
            """
        ).strip()

        logger.info("Prometheus queries: current, average, z_score")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            responses = []
            for q in (current_q, avg_q, z_q):
                async with session.get(f"{self.api_url}/query", params={"query": q}) as resp:
                    resp.raise_for_status()
                    responses.append(await resp.json())

            # Combine individual results into a single structure for formatting
            combined_results = []
            for r in responses:
                combined_results.extend(r.get("data", {}).get("result", []))
            combined = {"data": {"result": combined_results}}

            formatted_stats = self._format_stats(combined)
            logger.info(f"Metric analysis for host {host}: {formatted_stats}")
            return formatted_stats

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
@mcp_server.tool
async def discover_metrics(host: str, vm_url: Optional[str] = None) -> str:
    """
    MCP tool: discover_metrics

    Retrieve the list of metric names currently recorded for a specific host
    (matched using the Prometheus/VictoriaMetrics `instance` label).

    Args:
        host: IP address or hostname that appears in metric `instance` labels.

    Returns:
        JSON-encoded list of metric name strings.
    """
    tool = RedfishMetricTool()
    names = await tool.discover_metrics(host)
    return json.dumps(names)


@mcp_server.tool
async def get_metric_analysis(host: str, metric_name: str, window_hours: int = 3, vm_url: Optional[str] = None) -> str:
    """
    MCP tool: get_metric_analysis

    Fetch statistical analysis for a given `metric_name` on `host` over the
    specified `window_hours`. The analysis includes current value, average,
    and a z-score anomaly metric formatted as a human-readable string.

    Args:
        host: IP address or hostname that appears in metric `instance` labels.
        metric_name: The metric to analyze (Prometheus metric name).
        window_hours: Window size in hours for computing averages and stddev.

    Returns:
        JSON-encoded dict with key "analysis" containing a formatted string.
    """
    tool = RedfishMetricTool()
    analysis = await tool.get_metric_analysis(host, metric_name, window_hours)
    return json.dumps({"analysis": analysis})


@mcp_server.tool
def health_check() -> str:
    """MCP health-check tool returning a compact JSON status object."""
    return json.dumps({"status": "ok"})


if __name__ == "__main__":
    # Run over stdio for FastMCP transport
    mcp_server.run(transport="stdio")
