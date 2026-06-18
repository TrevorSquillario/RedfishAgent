package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/tmaxmax/go-sse"
)

// ─── iDRAC Log Event (Dell LCLog Entry) ───

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
		} `json:"Links"`
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

// ─── Inventory ───

type InventoryTarget struct {
	Targets []string          `json:"targets"`
	Labels  map[string]string `json:"labels"`
}

// ─── SSE Listener ───

type SSEListener struct {
	redisClient  *redis.Client
	httpClient   *http.Client
	ctx          context.Context
	inventoryURL string
	redisStream  string
}

// ─── Inventory ───

func (l *SSEListener) getInventory() ([]string, error) {
	log.Printf("[inventory] fetching inventory from %s", l.inventoryURL)

	req, err := http.NewRequestWithContext(l.ctx, "GET", l.inventoryURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating inventory request: %w", err)
	}

	resp, err := l.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetching inventory: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("inventory API returned status %d", resp.StatusCode)
	}

	var targets []InventoryTarget
	if err := json.NewDecoder(resp.Body).Decode(&targets); err != nil {
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

// ─── Redfish Auth (token-based) ───

func createRedfishSession(ctx context.Context, client *http.Client, host, user, pass string) (string, error) {
	sessionUrl := fmt.Sprintf("%s/redfish/v1/SessionService/Sessions", host)

	payload := map[string]string{
		"UserName": user,
		"Password": pass,
	}

	jsonPayload, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, sessionUrl, bytes.NewBuffer(jsonPayload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("unexpected status %s: %s", resp.Status, string(body))
	}

	token := resp.Header.Get("X-Auth-Token")
	if token == "" {
		return "", fmt.Errorf("X-Auth-Token header was missing from the response")
	}

	return token, nil
}

// ─── SSE per BMC ───

func (l *SSEListener) startAlertStream(bmcIP, user, pass string) {
	// Build token-authenticated SSE URL
	httpClient := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
		Timeout: 0,
	}

	ctx, cancel := context.WithCancel(l.ctx)
	defer cancel()

	var token string
	for {
		select {
		case <-l.ctx.Done():
			log.Printf("[auth] context done, stopping auth retry for %s", bmcIP)
			return
		default:
		}

		var authErr error
		token, authErr = createRedfishSession(ctx, httpClient, "https://"+bmcIP, user, pass)
		if authErr == nil {
			break
		}
		log.Printf("[auth] failed to create session for %s: %v — retrying in 30s", bmcIP, authErr)
		time.Sleep(30 * time.Second)
	}
	log.Printf("[auth] token acquired for %s", bmcIP)

	// Build SSE URL with $filter
	baseSseUrl, err := url.Parse(fmt.Sprintf("https://%s/redfish/v1/SSE", bmcIP))
	if err != nil {
		log.Printf("[sse] invalid URL for %s: %v", bmcIP, err)
		return
	}
	params := url.Values{}
	params.Add("$filter", "EventType eq Event")
	baseSseUrl.RawQuery = params.Encode()
	finalUrl := baseSseUrl.String()

	// Create go-sse client
	sseClient := &sse.Client{
		HTTPClient: httpClient,
		Backoff: sse.Backoff{
			MaxRetries: -1, // no auto-retry; we handle reconnect manually
		},
	}

	// Reconnect loop
	for {
		select {
		case <-l.ctx.Done():
			log.Printf("[sse] context done, stopping %s", bmcIP)
			return
		default:
		}

		req, connErr := http.NewRequestWithContext(ctx, http.MethodGet, finalUrl, nil)
		if connErr != nil {
			log.Printf("[sse] failed to create request for %s: %v", bmcIP, connErr)
			time.Sleep(5 * time.Second)
			continue
		}

		req.Header.Set("X-Auth-Token", token)
		req.Header.Set("Accept", "text/event-stream")

		conn := sseClient.NewConnection(req)

		conn.SubscribeMessages(func(event sse.Event) {
			l.handleEvent(bmcIP, event.Data)
		})

		log.Printf("[sse] connecting to %s", finalUrl)
		if connectErr := conn.Connect(); !errors.Is(connectErr, context.Canceled) {
			log.Printf("[sse] stream interrupted for %s: %v — reconnecting in 1s", bmcIP, connectErr)
		}

		time.Sleep(1 * time.Second)
	}
}

// ─── Event Processing & Redis Ingestion ───

func (l *SSEListener) handleEvent(bmcIP string, rawJson string) {
	if !strings.Contains(rawJson, "MessageId") {
		return
	}

	var payload RedfishSSEPayload
	err := json.Unmarshal([]byte(rawJson), &payload)

	if err != nil || len(payload.Events) == 0 {
		var singleEvent iDRACLogEvent
		if err := json.Unmarshal([]byte(rawJson), &singleEvent); err == nil && singleEvent.MessageID != "" {
			payload.Events = []iDRACLogEvent{singleEvent}
		} else {
			return
		}
	}

	for _, event := range payload.Events {
		// Marshal this specific event for raw_json
		eventRaw, marshalErr := json.Marshal(event)
		if marshalErr != nil {
			log.Printf("[parse] failed to marshal event for %s: %v", bmcIP, marshalErr)
			continue
		}

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
			"raw_json":     string(eventRaw),
		}

		data, marshalErr := json.Marshal(triagePayload)
		if marshalErr != nil {
			log.Printf("[parse] failed to marshal triage payload for %s: %v", bmcIP, marshalErr)
			continue
		}

		cmd := l.redisClient.XAdd(l.ctx, &redis.XAddArgs{
			Stream:     l.redisStream,
			Values:     map[string]interface{}{"event": string(data)},
			MaxLen:     0,
			Approx:     true,
			NoMkStream: false,
		})
		if err := cmd.Err(); err != nil {
			log.Printf("[redis] failed to push event for %s: %v", bmcIP, err)
		} else {
			log.Printf("[redis] pushed event to %s", string(data))
		}
	}
}

// ─── Main ───

func main() {
	redisHost := os.Getenv("REDIS_HOST")
	redisPort := os.Getenv("REDIS_PORT")
	redisStream := os.Getenv("REDIS_STREAM")
	idracUser := os.Getenv("IDRAC_USERNAME")
	idracPass := os.Getenv("IDRAC_PASSWORD")
	inventoryURL := os.Getenv("INVENTORY_URL")

	if redisHost == "" || redisPort == "" || redisStream == "" {
		log.Fatal("[redis] REDIS_HOST, REDIS_PORT, and REDIS_STREAM must be set")
	}
	if inventoryURL == "" {
		log.Fatal("[inventory] INVENTORY_URL must be set")
	}
	if idracUser == "" || idracPass == "" {
		log.Fatal("[auth] IDRAC_USERNAME and IDRAC_PASSWORD must be set")
	}

	// Redis client
	rdb := redis.NewClient(&redis.Options{
		Addr:         fmt.Sprintf("%s:%s", redisHost, redisPort),
		PoolSize:     100,
		MinIdleConns: 10,
	})

	if err := rdb.Ping(context.Background()).Err(); err != nil {
		log.Fatalf("[redis] cannot connect: %v", err)
	}

	// HTTP client (no timeout for SSE streams)
	httpClient := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
		Timeout: 0,
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	listener := &SSEListener{
		redisClient:  rdb,
		httpClient:   httpClient,
		ctx:          ctx,
		inventoryURL: inventoryURL,
		redisStream:  redisStream,
	}

	// Load BMCS from inventory
	bmcs, err := listener.getInventory()
	if err != nil {
		log.Fatalf("[inventory] failed: %v", err)
	}
	if len(bmcs) == 0 {
		log.Fatal("[inventory] no BMC targets found")
	}

	// Launch SSE listeners (one goroutine per BMC)
	for _, bmc := range bmcs {
		go listener.startAlertStream(bmc, idracUser, idracPass)
	}

	log.Printf("[main] listening on %d BMC(s), Ctrl+C to stop", len(bmcs))

	// Graceful shutdown on SIGINT / SIGTERM
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	log.Println("[main] shutting down…")
	cancel()
	rdb.Close()
	log.Println("[main] done")
}
