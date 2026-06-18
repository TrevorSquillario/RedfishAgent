package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
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

	// Keep main function alive until SIGTERM/SIGINT
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	<-sigs
	log.Println("Signal received: exiting")
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

		// 2) If Context was not provided by the event, fall back to the remote address
		//    Prefer the host (IP) portion without port to keep identification consistent.
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
		var top map[string]interface{}
		_ = json.Unmarshal(job.Payload, &top)

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
