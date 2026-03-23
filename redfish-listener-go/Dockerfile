
# Multi-stage build: compile with Go, run in a small Alpine image as non-root
FROM --platform=$BUILDPLATFORM golang:1.22.2-alpine AS builder
RUN apk add --no-cache ca-certificates git
WORKDIR /src
COPY . .

# Build a static binary for the builder's native architecture (allow buildx to select platform)
RUN CGO_ENABLED=0 \
	go build -ldflags "-s -w" -o /redfish-listener-go ./main.go

FROM alpine:3.18
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder /redfish-listener-go /usr/local/bin/redfish-listener-go
RUN chown app:app /usr/local/bin/redfish-listener-go
USER app
EXPOSE 8443
ENTRYPOINT ["/usr/local/bin/redfish-listener-go"]

