# RedfishAgent

## Architecture

Redfish <-- idrac_exporter (/metrics) <-- Prometheus <-- Prometheus AlertManager (webhook) --> RedfishAgent (/webhook/alerts/prometheus) --> Redis Stream (alerts)

Redis Stream (alerts) <-- RedfishAgent LLMService --> LangGraph (LLM w/ tool calling) --> RedfishAgent OutputPlugins

1. Prometheus calls the discovery endpoint at http://redfishagent:8000/api/inventory/targets defined in `victoria-metrics/prometheus.yml`
2. The RedfishAgent `InventoryService` calls all the inventory plugins defined in `redfishagent/plugins/inventory` 
3. Prometheus creates scrape jobs for each Redfish endpoint based on the provided inventory
4. When each scrape job is run it calls http://idrac_exporter:9348/metrics?target=redfish-testserver-0 as the target. The idrac_exporter queries the Redfish endpoints and presents a /metrics endpoint for the target.
5. Prometheus AlertManager is setup to trigger an alert on all Critical log messages. Defined in `victoria-metrics/rules/alerts-redfish-alerts.yml`. This triggers a webhook defined in `victoria-metrics/alertmanager.yml`
6. The RedfishAgent `WebhookService` receives this webhook at `/api/webhook/alerts/prometheus` in the `webhook_router.py`. This calls the `handle_prometheus` method of the `WebhookService` which sends the alert to the `alerts` Redis stream
7. The RedfishAgent `LLMService` listens to the `alerts` Redis stream and triggers the LangGraph workflow
8. The output of the LangGraph workflow is sent to all output plugins defined in `redfishagent/plugins/output` 

### Application Flow

```
    +------------------------+
    |  Prometheus Start      |
    | (victoria-metrics)     |
    +-----------+------------+
                |
(1) Calls Discovery Endpoint  | (/api/inventory/targets defined in victoria-metrics/prometheus.yml)
                |
                v
    +------------------------+
    |     RedfishAgent       |
    |  InventoryService      |
    +-----------+------------+
                |
(2) Calls Inventory Plugins   | (redfishagent/plugins/inventory)
                |
                v
    +------------------------+
    |  Inventory Plugins     |
    +-----------+------------+
                |
(2) Returns Target List      |
                |
                v
    +------------------------+
    |  Prometheus Server     |
    +-----------+------------+
                |
(3) Creates Scrape Jobs      |
                |
                v
    +-----------+------------+
    |  Scrape Job Execution  |
    +-----------+------------+
                |
(4) Calls /metrics?target=... |
                |
                v
    +------------------------+
    |     iDRAC Exporter     |
    +-----------+------------+
                |
(4) Queries Redfish Endpoints| (Hardware Data)
                |
                v
    +------------------------+
    |  Prometheus Server     |
    | (Evaluates Rules)      |
    +-----------+------------+
                |
(5) Critical Log Detected   | (rules/alerts-redfish-alerts.yml)
                |
                v
    +------------------------+
    |     AlertManager       |
    +-----------+------------+
                |
(5) Triggers Webhook         | (/api/webhook/prometheus defined in alertmanager.yml)
                |
                v
    +------------------------+
    |     RedfishAgent       |
    |   WebhookService       |
    | (webhook_router.py)    |
    +-----------+------------+
                |
(6) Calls handle_prometheus   | (Sends to Redis "alerts" stream)
                |
                v
    +------------------------+
    |     Redis Stream       |
    |      ("alerts")        |
    +-----------+------------+
                |
(7) Service Listens to Stream|
                |
                v
    +------------------------+
    |     RedfishAgent       |
    |      LLMService        |
    +-----------+------------+
                |
(7) Triggers LLM Workflow    |
                |
                v
    +------------------------+
    |   LangGraph Workflow   |
    +-----------+------------+
                |
(8) Sends Output             |
                |
                v
    +------------------------+
    |     Output Plugins     |
    | (plugins/output)       |
    +------------------------+
```