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
	"syscall"
	"time"

	"github.com/tmaxmax/go-sse"
)

// Define connection configuration
const (
	BmcHost  = "https://192.168.0.200" // Replace with your BMC IP/Hostname
	Username = "root"                  // Replace with your BMC username
	Password = "calvin"                // Replace with your BMC password
)

// SessionPayload mirrors the Redfish JSON body requirement for login
type SessionPayload struct {
	UserName string `json:"UserName"`
	Password string `json:"Password"`
}

func main() {
	// 1. Setup custom HTTP client to handle self-signed BMC certificates
	httpClient := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
		Timeout: 10 * time.Second,
	}

	log.Println("🔄 Creating Redfish Authentication Session...")
	authToken, err := createRedfishSession(httpClient, BmcHost, Username, Password)
	if err != nil {
		log.Fatalf("❌ Failed to create session: %v", err)
	}
	log.Println("✅ Session created successfully. Token acquired.")

	// 2. Remove timeout restriction on the HTTP Client for the long-lived SSE stream
	httpClient.Timeout = 0

	// 3. Setup SSE Request with properly encoded query parameters
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	baseSseUrl, err := url.Parse(fmt.Sprintf("%s/redfish/v1/SSE", BmcHost))
	if err != nil {
		log.Fatalf("❌ Invalid base URL: %v", err)
	}

	params := url.Values{}
	params.Add("$filter", "EventType eq Event")
	baseSseUrl.RawQuery = params.Encode()
	finalUrl := baseSseUrl.String()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, finalUrl, nil)
	if err != nil {
		log.Fatalf("❌ Failed to create SSE request: %v", err)
	}

	// Attach authentication token and headers
	req.Header.Set("X-Auth-Token", authToken)
	req.Header.Set("Accept", "text/event-stream")

	// 4. Create SSE client with custom HTTP client and no auto-retry
	// (Redfish SSE stream is long-lived, not a streaming LLM response)
	sseClient := &sse.Client{
		HTTPClient: httpClient,
		Backoff: sse.Backoff{
			MaxRetries: -1, // disable auto-retry; we handle shutdown via context
		},
	}

	// 5. Initiate connection with the request
	conn := sseClient.NewConnection(req)

	// Channel to catch OS shutdown signals so we can exit gracefully
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Printf("📡 Connecting to SSE Stream: %s\n", finalUrl)
		if err := conn.Connect(); !errors.Is(err, context.Canceled) {
			log.Printf("⚠️ Stream connection interrupted: %v", err)
		}
	}()

	// 6. Subscribe to all events and log complete output
	conn.SubscribeMessages(func(event sse.Event) {
		log.Println("--- 🔔 New Redfish Event Received ---")
		if event.LastEventID != "" {
			log.Printf("Event ID: %s\n", event.LastEventID)
		}
		if event.Type != "" {
			log.Printf("Event Type: %s\n", event.Type)
		}

		// Attempt to pretty-print the JSON data from the BMC
		var prettyJSON bytes.Buffer
		if err := json.Indent(&prettyJSON, []byte(event.Data), "", "  "); err == nil {
			log.Printf("Payload:\n%s\n", prettyJSON.String())
		} else {
			// Fallback to raw string if it's not valid JSON
			log.Printf("Payload (Raw): %s\n", event.Data)
		}
		log.Println("-------------------------------------")
	})

	// Block until Ctrl+C is pressed
	<-sigChan
	log.Println("🛑 Shutting down log listener...")
}

// createRedfishSession performs the POST request to exchange credentials for an X-Auth-Token
func createRedfishSession(client *http.Client, host, user, pass string) (string, error) {
	sessionUrl := fmt.Sprintf("%s/redfish/v1/SessionService/Sessions", host)

	payload := SessionPayload{
		UserName: user,
		Password: pass,
	}

	jsonPayload, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}

	req, err := http.NewRequest(http.MethodPost, sessionUrl, bytes.NewBuffer(jsonPayload))
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

	// The session token is returned in the HTTP Header, NOT the JSON body.
	token := resp.Header.Get("X-Auth-Token")
	if token == "" {
		return "", fmt.Errorf("X-Auth-Token header was missing from the response")
	}

	return token, nil
}
