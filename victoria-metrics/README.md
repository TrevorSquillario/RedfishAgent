This folder holds Prometheus scraper configuration for the local VictoriaMetrics instance.

Files:
- `prometheus.yml` - Prometheus config that scrapes `idrac_exporter` and remote_writes to VictoriaMetrics.
- `data/` - Prometheus storage and VictoriaMetrics data (mounted by Compose).

Notes:
- The `idrac_exporter` target uses port `9180` in the example; update `prometheus.yml` if your exporter uses a different port.
- Start the stack with `docker compose up -d` from the repository root.
- Adjust scrape intervals and targets to match your environment.
