from typing import Any, Dict, List
import os
import yaml
from fastapi import FastAPI, Response, status

app = FastAPI(title="Inventory HTTP SD for VictoriaMetrics")

CONFIG_PATH = os.environ.get("INVENTORY_CONFIG_PATH", "/config/inventory.yaml")


def load_inventory(path: str) -> List[Dict[str, Any]]:
    """Load YAML inventory and convert to Prometheus HTTP SD format.

    Supported input forms:
    - list of target strings: ["host:port", "host2:port"]
    - list of dicts with `targets` and optional `labels` (Prom-style)
    - list of dicts with host/port/name/labels fields
    """
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return []

    if data is None:
        return []

    out: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        # try to handle a top-level dict with `targets` key
        if "targets" in data and isinstance(data["targets"], list):
            return normalize_entries(data["targets"])
        # otherwise wrap
        data = [data]

    if isinstance(data, list):
        out = normalize_entries(data)

    return out


def normalize_entries(entries: List[Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for item in entries:
        if isinstance(item, str):
            results.append({"targets": [item]})
            continue

        if isinstance(item, dict):
            # If already in Prom HTTP SD form
            if "targets" in item:
                t = item.get("targets") or []
                labels = item.get("labels") or item.get("labels", {})
                entry = {"targets": ensure_list_of_str(t)}
                if labels:
                    entry["labels"] = labels
                results.append(entry)
                continue

            # Try to build a target from host/ip/name + port
            host = item.get("host") or item.get("address") or item.get("ip") or item.get("name")
            port = item.get("port", 443) or item.get("metrics_port", 443)
            labels = item.get("labels") or {}
            if host and port:
                results.append({"targets": [f"{host}:{port}"], "labels": labels})
                continue

            # Fallback: any dict containing a `target` key
            if "target" in item:
                results.append({"targets": ensure_list_of_str(item.get("target")), "labels": labels})
                continue

        # ignore unknown types
    return results


def ensure_list_of_str(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


@app.get("/api/v1/targets")
def get_targets():
    targets = load_inventory(CONFIG_PATH)
    return targets


@app.get("/healthz")
def healthz():
    return Response(content="ok", media_type="text/plain", status_code=status.HTTP_200_OK)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), reload=False)
