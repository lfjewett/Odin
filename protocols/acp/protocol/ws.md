# WebSocket Protocol Specification

## Endpoint

```
ws://localhost:8000/ws
```

*Port and host vary by deployment; adjust accordingly.*

## Connection Lifecycle

### 1. Connect
Client initiates WebSocket handshake to `/ws`.

```javascript
const ws = new WebSocket("ws://localhost:8000/ws");
```

### 2. Receive Events
Server immediately begins sending `EventEnvelope` messages (one per line as JSON).

```json
{"event":{"event_id":"spy-0",...}}
{"event":{"event_id":"spy-1",...}}
{"event":{"event_id":"spy-2",...}}
```

### 3. Reconnect on Disconnect
If connection closes (client crash, network blip, server restart):
1. Browser detects `onclose`
2. Wait 3 seconds (exponential backoff in production)
3. Reconnect with same endpoint
4. Server re-sends recent candles (in-memory buffer in Phase 0; durable store in Phase 1+)

### 4. Disconnect
When client closes connection:
```javascript
ws.close();
```

## Message Format

### Server → Client: EventEnvelope

```typescript
interface EventEnvelope {
  event: MarketCandle | null;
}

interface MarketCandle {
  event_id: string;       // Dedup identifier
  trace_id: string;       // Trace context
  symbol: string;         // "SPY"
  ts_event: string;       // ISO 8601 timestamp
  ts_ingest: string;      // ISO 8601 timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  message_type: "price";
  schema_version: string; // "1.0"
}
```

### Client → Server
Currently **no client-to-server messages** in Phase 0. Future phases may support:
- Subscription updates
- Filter/symbol changes
- Heartbeat responses

## Keepalive / Heartbeat

No keepalive currently implemented in Phase 0. Server continuously broadcasts. If no events for 60 seconds, consider adding:

```json
{"event": null}
```

## Error Handling

### Malformed JSON
If client receives invalid JSON, it should:
1. Log the error
2. Continue listening for next message
3. **Never** close the connection on parse error

### Connection Loss
- Browser automatically detects `close` event
- Implements 3-second reconnect delay
- Retries indefinitely until success

### Duplicate Events
- Client deduplicates using `event_id`
- No server-side resend required (at-least-once delivery)

## Performance Characteristics

### Throughput
- **SPY 1-minute candles:** 1 message per second (~200 bytes/msg)
- **Aggregate throughput:** ~1.6 Mbps per 100 concurrent clients
- **Backend scalability:** Uvicorn handles 1000+ concurrent WebSocket connections on single 2-core machine

### Latency
- **Producer → Backend → Browser:** < 100ms (local network)
- **Reconnect latency:** 3–5 seconds (configurable)

### Buffer Sizing
- **Backend in-memory buffer:** 1000 recent candles per symbol (~200 KB)
- **Browser in-memory state:** Full chart data + dedupe cache (varies by timeframe)

## Security

### Phase 0
- No authentication
- WebSocket unencrypted (`ws://` not `wss://`)
- Single-user product
- Suitable for local development and trusted networks only

### Future (Phase 3+)
- Add `wss://` for TLS encryption
- Add bearer token or session cookie authentication
- Add per-symbol subscription ACLs

## Debugging

### Server Logs
```bash
docker-compose logs -f odin | grep ws
```

Look for:
- `ws_connect` events (new client)
- `ws_disconnect` events (client gone)
- `ws_send_error` (failed to send to client)

### Browser DevTools
1. Open Chrome DevTools → **Network** tab
2. Filter for WebSocket: type `ws` in filter
3. Click connection to inspect frames
4. See event payloads in real-time

### Manual Testing
```bash
# macOS/Linux
wscat -c ws://localhost:8000/ws

# Should see candles printed every second:
# {"event":{"event_id":"spy-0",...}}
```

## References
- [MDN WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
- [RFC 6455 – WebSocket Protocol](https://tools.ietf.org/html/rfc6455)
