package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// iDRACLogEvent perfectly maps the Dell LCLog Entry payload
type iDRACLogEvent struct {
	ODataContext string `json:"@odata.context"`
	ODataID      string `json:"@odata.id"`
	Created      string `json:"Created"`
	Description  string `json:"Description"`
	EntryType    string `json:"EntryType"`
	ID           string `json:"Id"`
	Links        struct {
		OriginOfCondition struct {
			ODataID string `json:"@odata.id"`
		} `json:"OriginOfCondition"`
	} `json:"Links"`
	Message     string   `json:"Message"`
	MessageArgs []string `json:"MessageArgs"`
	MessageID   string   `json:"MessageId"`
	Name        string   `json:"Name"`
	Severity    string   `json:"Severity"`
	Oem         struct {
		Dell struct {
			AgentID  string `json:"AgentID"`
			Category string `json:"Category"`
			BitMask  string `json:"BitMask"`
		} `json:"Dell"`
	} `json:"Oem"`
}

// RedfishSSEPayload handles the standard Redfish Event wrapper
type RedfishSSEPayload struct {
	Events []iDRACLogEvent `json:"Events"`
}

// Simplified Redfish Event Structure for Alerts
type RedfishEventArray struct {
	Events []struct {
		EventId           string `json:"EventId"`
		Severity          string `json:"Severity"` // e.g., Critical, Warning, OK
		Message           string `json:"Message"`
		MessageId         string `json:"MessageId"`
		OriginOfCondition string `json:"OriginOfCondition"` // e.g., /redfish/v1/Chassis/System.Embedded.1/Thermal
		EventTimestamp    string `json:"EventTimestamp"`
	} `json:"Events"`
}

type SSEListener struct {
	redisClient  *redis.Client
	httpClient   *http.Client
	ctx          context.Context
	inventoryURL string
}

// InventoryTarget represents a single entry from the inventory API
type InventoryTarget struct {
	Targets []string          `json:"targets"`
	Labels  map[string]string `json:"labels"`
}

// getInventory fetches the list of BMC targets from the inventory API
func (l *SSEListener) getInventory() ([]string, error) {
	log.Printf("[inventory] fetching inventory from %s", l.inventoryURL)

	req, err := http.NewRequestWithContext(l.ctx, "GET", l.inventoryURL, nil)
	if err != nil {
		log.Printf("[inventory] error creating request: %v", err)
		return nil, fmt.Errorf("creating inventory request: %w", err)
	}

	resp, err := l.httpClient.Do(req)
	if err != nil {
		log.Printf("[inventory] error fetching inventory: %v", err)
		return nil, fmt.Errorf("fetching inventory: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("[inventory] unexpected status code: %d", resp.StatusCode)
		return nil, fmt.Errorf("inventory API returned status %d", resp.StatusCode)
	}

	var targets []InventoryTarget
	if err := json.NewDecoder(resp.Body).Decode(&targets); err != nil {
		log.Printf("[inventory] error decoding inventory response: %v", err)
		return nil, fmt.Errorf("decoding inventory: %w", err)
	}

	var bmcs []string
	for _, t := range targets {
		for _, bmc := range t.Targets {
			bmcs = append(bmcs, bmc)
		}
		log.Printf("[inventory] discovered %d target(s) from entry with labels: %v", len(t.Targets), t.Labels)
	}

	log.Printf("[inventory] loaded %d BMC(s) from inventory", len(bmcs))
	return bmcs, nil
}

func main() {
	// Read environment variables
	redisHost := os.Getenv("REDIS_HOST")
	redisPort := os.Getenv("REDIS_PORT")
	redisStream := os.Getenv("REDIS_STREAM")
	idracUser := os.Getenv("IDRAC_USERNAME")
	idracPass := os.Getenv("IDRAC_PASSWORD")
	idracSSLVerify := os.Getenv("IDRAC_SSL_VERIFY")

	if redisHost == "" {
		log.Fatal("[redis] REDIS_HOST environment variable is not set")
	}
	if redisPort == "" {
		log.Fatal("[redis] REDIS_PORT environment variable is not set")
	}
	if redisStream == "" {
		log.Fatal("[redis] REDIS_STREAM environment variable is not set")
	}

	// Initialize high-throughput Redis client
	rdb := redis.NewClient(&redis.Options{
		Addr:         fmt.Sprintf("%s:%s", redisHost, redisPort),
		PoolSize:     100, // Important for scaling concurrent writes
		MinIdleConns: 10,
	})

	// Setup HTTP transport based on IDRAC_SSL_VERIFY
	insecure := idracSSLVerify != "true" && idracSSLVerify != "1"
	tr := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: insecure},
	}

	listener := &SSEListener{
		redisClient:  rdb,
		httpClient:   &http.Client{Transport: tr, Timeout: 0}, // 0 means no timeout for persistent streams
		ctx:          context.Background(),
		inventoryURL: os.Getenv("INVENTORY_URL"),
	}

	if listener.inventoryURL == "" {
		log.Fatal("[inventory] INVENTORY_URL environment variable is not set")
	}
	if idracUser == "" {
		log.Fatal("[auth] IDRAC_USERNAME environment variable is not set")
	}
	if idracPass == "" {
		log.Fatal("[auth] IDRAC_PASSWORD environment variable is not set")
	}

	// Load BMCS from inventory API
	bmcs, err := listener.getInventory()
	if err != nil {
		log.Fatalf("[inventory] failed to load inventory: %v", err)
	}

	if len(bmcs) == 0 {
		log.Fatal("[inventory] no BMC targets found in inventory")
	}

	for _, bmc := range bmcs {
		log.Printf("[sse] launching SSE listener for %s", bmc)
		// Launch each connection into its own lightweight goroutine (~2KB memory footprint each)
		go listener.startAlertStream(bmc, idracUser, idracPass)
	}

	// Keep main alive
	select {}
}

func (l *SSEListener) startAlertStream(bmcIP, user, pass string) {
	url := "https://" + bmcIP + "/redfish/v1/SSE?$filter=EventType eq 'Event'"
	log.Printf("[sse] connecting to %s", url)

	for {
		select {
		case <-l.ctx.Done():
			log.Printf("[sse] context done, stopping %s", bmcIP)
			return
		default:
			req, _ := http.NewRequestWithContext(l.ctx, "GET", url, nil)
			req.SetBasicAuth(user, pass)
			req.Header.Set("Accept", "text/event-stream")

			resp, err := l.httpClient.Do(req)
			if err != nil {
				log.Printf("[sse] connection error for %s: %v", bmcIP, err)
				time.Sleep(5 * time.Second)
				continue
			}
			log.Printf("[sse] connected to %s (status: %d)", bmcIP, resp.StatusCode)

			// Borrowed concept from Dell reference: Scan the live stream line by line
			scanner := bufio.NewScanner(resp.Body)
			var sb strings.Builder

			for scanner.Scan() {
				line := scanner.Text()

				// SSE lines starting with data: contain our JSON payload
				if strings.HasPrefix(line, "data:") {
					sb.WriteString(strings.TrimPrefix(line, "data:"))

					// Often payloads span across lines or terminate. If valid complete JSON:
					rawJson := sb.String()

					// Offload parsing and Redis ingestion quickly
					go l.processAndIngest(bmcIP, rawJson)

					sb.Reset()
				}
			}
			if err := scanner.Err(); err != nil {
				log.Printf("[sse] scanning error for stream %s: %v", bmcIP, err)
			}
			resp.Body.Close()
			log.Printf("[sse] stream closed for %s, reconnecting in 1s", bmcIP)

			// If stream breaks, wait a second and let the loop reconnect
			time.Sleep(1 * time.Second)
		}
	}
}

func (l *SSEListener) processAndIngest(bmcIP string, rawJson string) {
	// Fast abort if it doesn't look like an event payload
	if !strings.Contains(rawJson, "MessageId") {
		return
	}

	var payload RedfishSSEPayload

	// Try unmarshaling assuming it's wrapped in an "Events" array (Standard Redfish SSE behavior)
	err := json.Unmarshal([]byte(rawJson), &payload)

	// Fallback: If iDRAC pushes the raw object directly without the "Events" array
	if err != nil || len(payload.Events) == 0 {
		log.Printf("[parse] failed to unmarshal Events array for %s: %v", bmcIP, err)
		var singleEvent iDRACLogEvent
		if err := json.Unmarshal([]byte(rawJson), &singleEvent); err == nil && singleEvent.MessageID != "" {
			payload.Events = []iDRACLogEvent{singleEvent}
		} else {
			// Not a valid event or parsing failed
			log.Printf("[parse] fallback unmarshal also failed for %s: %v", bmcIP, err)
			return
		}
	}

	// Iterate through events and push individually to Redis for clean downstream agent processing
	for _, event := range payload.Events {

		// Package an enriched object specifically designed for a triage worker
		triagePayload := map[string]interface{}{
			"bmc_ip":       bmcIP,
			"timestamp":    time.Now().Unix(),
			"event_time":   event.Created,
			"severity":     event.Severity,
			"message_id":   event.MessageID,
			"message":      event.Message,
			"args":         event.MessageArgs,
			"category":     event.Oem.Dell.Category,
			"component_id": event.Links.OriginOfCondition.ODataID,
			"raw_json":     rawJson, // Keep raw data in case the agent needs deeper context
		}

		data, err := json.Marshal(triagePayload)
		if err != nil {
			continue
		}

		// Push to the Redis stream
		// l.redisClient.XAdd(l.ctx, &redis.XAddArgs{Stream: redisStream, Values: data})
		log.Printf("[redis] pushed event to %s", string(data))
	}
}
