# RedfishAgent

## Architecture

Redfish <-- idrac_exporter (/metrics) <-- Prometheus <-- Prometheus AlertManager (webhook) --> RedfishAgent (/webhook/alerts/prometheus) --> Redis Stream (alerts)

Redis Stream (alerts) <-- RedfishAgent LLMService --> LangGraph (LLM w/ tool calling) --> RedfishAgent OutputPlugins

1. Prometheus calls the discovery endpoint at http://redfishagent:8000/api/inventory/targets defined in `victoria-metrics/prometheus.yml`
2. The InventoryService calls all the inventory plugins defined in `redfishagent/plugins/inventory` 
3. Prometheus creates scrape jobs based on the provided inventory
4. When each scrape job is run it calls http://idrac_exporter:9348/metrics?target=redfish-testserver-0 as the target. The idrac_exporter queries the Redfish endpoints and presents a /metrics endpoint for the target.
5. Prometheus AlertManager is setup to trigger an alert on all Critical log messages. Defined in `victoria-metrics/rules/alerts-redfish-alerts.yml`. This triggers a webhook defined in `victoria-metrics/alertmanager.yml`
6. The RedfishAgent FastAPI receives this webhook at `/api/webhook/alerts/prometheus` in the `webhook_router.py`. This calls the `handle_prometheus` method of the `WebhookService` which sends the alert to the `alerts` Redis stream
7. The `LLMService` listens to the `alerts` Redis stream and triggers the LangGraph workflow
8. The output of the LangGraph workflow is sent to all output plugins defined in `redfishagent/plugins/output` 