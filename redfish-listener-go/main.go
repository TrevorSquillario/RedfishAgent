package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

// --- Structs for Ingestion ---

type RedfishEvent struct {
	Id      string `json:"Id"`
	Context string `json:"Context,omitempty"`
	Events  []struct {
		EventId   string `json:"EventId"`
		MessageId string `json:"MessageId"`
		Severity  string `json:"Severity"`
	} `json:"Events"`
}

type Job struct {
	Payload []byte
	Source  string
}

// --- Structs for Subscription (Based on DMTF Schema) ---

type SubscriptionPayload struct {
	Destination      string   `json:"Destination"`
	Types            []string `json:"EventTypes,omitempty"`
	RegistryPrefixes []string `json:"RegistryPrefixes,omitempty"`
	Context          string   `json:"Context"`
	Protocol         string   `json:"Protocol"`
}

type Endpoint struct {
	Host     string
	Port     int
	Username string
	Password string
}

const (
	MaxWorkers = 100
	MaxQueue   = 10000
)

// Redis config (default to redis://redis:6379)
var RedisURL = "redis://redis:6379"
var RedisClient *redis.Client

var RedisStream = "redfish_events"

// Listener destination (what other endpoints will POST to). Can be overridden
// by setting the LISTENER_DEST env var (e.g. https://host.example.com:8443/redfish/events)
var ListenerIP = "http://127.0.0.1:8443/redfish/events"

var JobQueue chan Job

type SubContext struct {
	EP      Endpoint
	UnsubID string
}

var Subscriptions []SubContext

// RemoteMappings holds mappings from remote connection IP -> inventory hostname/IP
var RemoteMappings []map[string]string
var RemoteMapMu sync.Mutex

func main() {
	JobQueue = make(chan Job, MaxQueue)

	// Ensure logs go to stdout so Docker captures them consistently
	log.SetOutput(os.Stdout)

	// Allow overriding the public listener destination via env
	if v := os.Getenv("LISTENER_DEST"); v != "" {
		ListenerIP = v
	}

	// 1. Initialize Redis and start the Worker Pool for ingestion
	if v := os.Getenv("REDIS_URL"); v != "" {
		RedisURL = v
	}

	// Allow overriding the Redis stream name via env var REDIS_STREAM
	if v := os.Getenv("REDIS_STREAM"); v != "" {
		RedisStream = v
	}
	opts, err := redis.ParseURL(RedisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL %q: %v", RedisURL, err)
	}
	RedisClient = redis.NewClient(opts)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := RedisClient.Ping(ctx).Err(); err != nil {
		log.Fatalf("unable to connect to redis %s: %v", RedisURL, err)
	}
	log.Printf("Connected to Redis at %s", RedisURL)

	for i := 1; i <= MaxWorkers; i++ {
		go worker(i, JobQueue)
	}

	// 2. Start the HTTP Listener in the background
	go startListener()

	// 3. Execute Subscriptions (create Redfish subscriptions on each endpoint)
	// In reality, this list comes from your inventory database or MCP gateway
	invURL := os.Getenv("INVENTORY_URL")
	if invURL == "" {
		invURL = "http://redfishalerts:8080/api/v1/targets"
	}

	endpoints, err := fetchEndpoints(invURL)
	if err != nil || len(endpoints) == 0 {
		log.Printf("Failed to load endpoints from %s: %v; falling back to static list", invURL, err)
		endpoints = []Endpoint{}
	}

	log.Printf("Initiating Redfish subscriptions for %d endpoints...", len(endpoints))
	for _, ep := range endpoints {
		unsubID, err := createRedfishSubscription(ep)
		if err != nil {
			log.Printf("Subscription failed for %s: %v", ep.Host, err)
			continue
		}
		if unsubID != "" {
			Subscriptions = append(Subscriptions, SubContext{EP: ep, UnsubID: unsubID})
		}
	}

	// Handle SIGTERM to clean up subscriptions on shutdown
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		<-sigs
		log.Println("Signal received: cleaning up subscriptions and exiting")
		cleanSubscriptions()
		os.Exit(0)
	}()

	// Keep main function alive
	select {}
}

// --- Subscription Logic ---
// createRedfishSubscription creates a Redfish event subscription on the given endpoint
// and returns the subscription Id (if available) for later deletion.
func createRedfishSubscription(ep Endpoint) (string, error) {
	// Prefer a stable inventory identifier in Context where available (see OpenBMC
	// Redfish EventService design: https://github.com/openbmc/docs/raw/refs/heads/master/designs/redfish-eventservice.md)
	// Start with the host as the default Context value. Inventory identifiers
	// were removed from the Endpoint representation; using the host keeps
	// Context stable and human-readable.
	ctx := ep.Host

	payload := SubscriptionPayload{
		Destination: ListenerIP,
		// Subscribe for Alert events (matches the python listener behavior)
		Types:    []string{"Alert"},
		Context:  ctx,
		Protocol: "Redfish",
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal payload: %w", err)
	}

	url := fmt.Sprintf("https://%s:%d/redfish/v1/EventService/Subscriptions", ep.Host, ep.Port)
	// Log request details (avoid logging secrets)
	log.Printf("Creating subscription on %s:%d -> %s (types=%v) url=%s", ep.Host, ep.Port, ListenerIP, payload.Types, url)

	req, err := http.NewRequest(http.MethodPost, url, bytes.NewBuffer(jsonData))
	if err != nil {
		return "", fmt.Errorf("create request: %w", err)
	}

	req.SetBasicAuth(ep.Username, ep.Password)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	var remoteAddr string
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			conn, err := dialer.DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			if conn != nil {
				ra := conn.RemoteAddr().String()
				if host, _, err := net.SplitHostPort(ra); err == nil {
					remoteAddr = host
				} else {
					remoteAddr = ra
				}
			}
			return conn, nil
		},
	}
	client := &http.Client{
		Timeout:   10 * time.Second,
		Transport: transport,
	}

	resp, err := client.Do(req)
	if err != nil {
		log.Printf("Request to %s failed: %v", url, err)
		return "", fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	bodyText := string(bodyBytes)
	if len(bodyText) > 500 {
		bodyText = bodyText[:500] + "..."
	}

	log.Printf("Subscription response from %s: status=%d url=%s body=%q", ep.Host, resp.StatusCode, url, bodyText)

	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusNoContent {
		return "", fmt.Errorf("status %d: %s", resp.StatusCode, bodyText)
	}

	// Try to determine subscription Id from response body or Location header
	var unsubID string
	// Location header may contain the subscription URI
	if loc := resp.Header.Get("Location"); loc != "" {
		// last path segment is likely the Id
		// quick parse: find last '/'
		last := -1
		for i := len(loc) - 1; i >= 0; i-- {
			if loc[i] == '/' {
				last = i
				break
			}
		}
		if last >= 0 && last < len(loc)-1 {
			unsubID = loc[last+1:]
		} else {
			unsubID = loc
		}
	}

	if unsubID == "" && len(bodyBytes) > 0 {
		var bodyMap map[string]interface{}
		if err := json.Unmarshal(bodyBytes, &bodyMap); err == nil {
			if v, ok := bodyMap["Id"].(string); ok {
				unsubID = v
			}
		}
	}

	log.Printf("Successfully subscribed to %s (id=%s) status=%d url=%s", ep.Host, unsubID, resp.StatusCode, url)

	if remoteAddr != "" {
		RemoteMapMu.Lock()
		RemoteMappings = append(RemoteMappings, map[string]string{remoteAddr: ep.Host})
		RemoteMapMu.Unlock()
		log.Printf("Recorded remote mapping: %s -> %s", remoteAddr, ep.Host)
	}

	return unsubID, nil
}

// fetchEndpoints loads Prometheus HTTP SD entries from the inventory service
// and converts them into Endpoint objects. It expects the HTTP SD format
// (a JSON list of objects with `targets` (list of strings) and optional `labels`).
func fetchEndpoints(url string) ([]Endpoint, error) {
	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
	}

	resp, err := client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("status %d: %s", resp.StatusCode, string(body))
	}

	var entries []map[string]interface{}
	dec := json.NewDecoder(resp.Body)
	if err := dec.Decode(&entries); err != nil {
		return nil, fmt.Errorf("decode failed: %w", err)
	}

	var out []Endpoint
	for _, e := range entries {
		var targets []interface{}
		if t, ok := e["targets"].([]interface{}); ok {
			targets = t
		} else if t2, ok := e["targets"].([]string); ok {
			for _, s := range t2 {
				targets = append(targets, s)
			}
		}

		// collect labels (optional)
		labels := map[string]string{}
		if lm, ok := e["labels"].(map[string]interface{}); ok {
			for k, v := range lm {
				labels[k] = fmt.Sprintf("%v", v)
			}
		}

		for _, t := range targets {
			ts := fmt.Sprintf("%v", t)
			host := ts
			port := 443
			if strings.Contains(ts, ":") {
				parts := strings.Split(ts, ":")
				host = parts[0]
				if p, err := strconv.Atoi(parts[1]); err == nil {
					port = p
				}
			}

			user := labels["username"]
			if user == "" {
				user = labels["user"]
			}
			pass := labels["password"]
			if pass == "" {
				pass = labels["pass"]
			}

			out = append(out, Endpoint{Host: host, Port: port, Username: user, Password: pass})
		}
	}

	return out, nil
}

// cleanSubscriptions attempts to delete created subscriptions on each endpoint
func cleanSubscriptions() {
	for _, s := range Subscriptions {
		if s.UnsubID == "" {
			continue
		}
		url := fmt.Sprintf("https://%s:%d/redfish/v1/EventService/Subscriptions/%s", s.EP.Host, s.EP.Port, s.UnsubID)
		log.Printf("Deleting subscription id=%s on %s:%d", s.UnsubID, s.EP.Host, s.EP.Port)
		req, err := http.NewRequest(http.MethodDelete, url, nil)
		if err != nil {
			log.Printf("Failed to create delete request for %s: %v", s.EP.Host, err)
			continue
		}
		req.SetBasicAuth(s.EP.Username, s.EP.Password)
		client := &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
			},
		}
		resp, err := client.Do(req)
		if err != nil {
			log.Printf("Failed to delete subscription %s on %s: %v", s.UnsubID, s.EP.Host, err)
			continue
		}
		bodyBytes, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		bodyText := string(bodyBytes)
		if len(bodyText) > 500 {
			bodyText = bodyText[:500] + "..."
		}
		log.Printf("Delete response from %s: status=%d body=%q", s.EP.Host, resp.StatusCode, bodyText)
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			log.Printf("Deleted subscription %s on %s (status=%d)", s.UnsubID, s.EP.Host, resp.StatusCode)
		} else {
			log.Printf("Failed to delete subscription %s on %s (status=%d)", s.UnsubID, s.EP.Host, resp.StatusCode)
		}
	}
}

// --- Ingestion Logic ---

func startListener() {
	http.HandleFunc("/redfish/events", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		body, _ := io.ReadAll(r.Body)
		defer r.Body.Close()

		// Resolve the event `source` in a well-defined priority order:
		//  1) Event `Context` field (preferred and authoritative when present)
		//  2) Recorded RemoteMappings (mapping of remote connection IP -> inventory host/IP)
		//  3) RemoteAddr of the incoming HTTP connection (raw IP, prefer no port)
		// This makes resolution deterministic and ensures stable inventory identifiers
		// are used when available (Context or mappings), otherwise fall back to
		// the network source address.

		source := ""

		// 1) Try to set from event Context first. If the event provides a
		//    Context it should be treated as the authoritative source value.
		var evt RedfishEvent
		if err := json.Unmarshal(body, &evt); err == nil {
			if evt.Context != "" {
				source = evt.Context
			}
		}

		// 2) If Context was not provided by the event, try recorded RemoteMappings.
		//    RemoteMappings were recorded at subscription time and map the remote
		//    connection address (IP or ip:port) to the inventory host/IP. Use the
		//    mapping only when we don't already have a Context value.
		if source == "" {
			RemoteMapMu.Lock()
			for _, m := range RemoteMappings {
				// Check for exact match (address with port) first
				if v, ok := m[r.RemoteAddr]; ok {
					source = v
					break
				}
				// Also check using only the host portion (strip port) to match how
				// some mappings may have been stored.
				if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
					if v, ok := m[host]; ok {
						source = v
						break
					}
				}
			}
			RemoteMapMu.Unlock()
		}

		// 3) Fallback: if neither Context nor a mapping produced a value,
		//    use the remote address. Prefer the host (IP) portion without port
		//    to keep identification consistent.
		if source == "" {
			source = r.RemoteAddr
			if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
				source = host
			}
		}

		select {
		case JobQueue <- Job{Payload: body, Source: source}:
			w.WriteHeader(http.StatusAccepted)
		default:
			http.Error(w, "Queue Full", http.StatusServiceUnavailable)
		}
	})

	// TLS cert/key must be provided via env vars. If either is missing,
	// fall back to plain HTTP (TLS disabled).
	certFile := os.Getenv("LISTENER_CERT")
	keyFile := os.Getenv("LISTENER_KEY")

	useTLS := certFile != "" && keyFile != ""

	if useTLS {
		// Ensure files exist before attempting to start TLS server
		if _, err := os.Stat(certFile); err != nil {
			log.Fatalf("TLS cert not found: %v", err)
		}
		if _, err := os.Stat(keyFile); err != nil {
			log.Fatalf("TLS key not found: %v", err)
		}

		log.Println("Listener started on :8443 (TLS)")
		// Use ListenAndServeTLS so the server accepts TLS connections like the python implementation
		if err := http.ListenAndServeTLS(":8443", certFile, keyFile, nil); err != nil {
			log.Fatalf("Server crashed: %v", err)
		}
	} else {
		// TLS disabled; run plain HTTP. Use port 8080 to avoid clashing with typical TLS port.
		log.Println("TLS disabled (LISTENER_CERT or LISTENER_KEY not set); starting listener on :8080 (HTTP)")
		if err := http.ListenAndServe(":8080", nil); err != nil {
			log.Fatalf("Server crashed: %v", err)
		}
	}
}

func worker(id int, jobs <-chan Job) {
	for job := range jobs {
		// Detect MetricReport payloads by parsing the JSON and checking
		// the @odata.type field. Skip sending MetricReports to Redis.
		var top map[string]interface{}
		if err := json.Unmarshal(job.Payload, &top); err == nil {
			if t, ok := top["@odata.type"].(string); ok {
				if strings.Contains(t, "MetricReport") {
					log.Printf("[Worker %d] Skipping MetricReport from %s (odata.type=%s)", id, job.Source, t)
					continue
				}
			}
		}

		// Push one Redis stream entry per Events item (expecting the same payload shape)
		if RedisClient != nil {
			if evs, ok := top["Events"].([]interface{}); ok && len(evs) > 0 {
				for _, ev := range evs {
					evPayload := map[string]interface{}{"Events": []interface{}{ev}}
					// Preserve a few top-level metadata fields when present
					for _, k := range []string{"Id", "Name", "@odata.type", "Oem"} {
						if v, ok := top[k]; ok {
							evPayload[k] = v
						}
					}
					b, _ := json.Marshal(evPayload)
					vals := map[string]interface{}{
						"source":  job.Source,
						"payload": string(b),
					}
					xid, err := RedisClient.XAdd(context.Background(), &redis.XAddArgs{
						Stream: RedisStream,
						Values: vals,
					}).Result()
					if err != nil {
						log.Printf("[Worker %d] Redis XAdd failed: %v", id, err)
					} else {
						log.Printf("[Worker %d] Pushed event from %s to stream %s id=%s", id, job.Source, RedisStream, xid)
					}
				}
			} else {
				// No Events present — do not push a fallback raw payload (per new expectation)
				log.Printf("[Worker %d] No Events array in payload from %s, skipping Redis push", id, job.Source)
			}
		}

		var event RedfishEvent
		if err := json.Unmarshal(job.Payload, &event); err == nil {
			for _, e := range event.Events {
				log.Printf("[Worker %d] Alert from %s: %s", id, job.Source, e.MessageId)
			}
		}
	}
}
