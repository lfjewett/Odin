# Migration Guide: ACP v0.3.0 → v0.4.0

**Target Date:** 2026-03-09  
**Breaking Changes:** Yes  
**Estimated Migration Effort:** 4-8 hours per component

---

## Executive Summary

ACP v0.4.0 introduces **mandatory chunking** to reliably handle large-scale historical data (3+ years of 1-minute candles = ~1.5M records). This migration is required for all backend routers, price agents, and indicator agents.

### Why This Change?

**Problem:** In ACP v0.3.0, sending 7,500+ candles in a single WebSocket message caused disconnects (code 1009: message too big). After reconnect, agents never received history again, making indicators appear "stuck."

**Solution:** ACP v0.4.0 mandates chunked delivery for `history_push` and `history_response`, with explicit transport limits negotiated via metadata.

---

## Breaking Changes Summary

| Change | Required Action | Impact |
|--------|----------------|--------|
| **Spec version** | Update all messages to `"spec_version": "ACP-0.4.0"` | All components |
| **Chunking mandatory** | Implement chunk send/receive logic | Backend + Agents |
| **Transport limits in metadata** | Add `transport_limits` object to `/metadata` | All agents |
| **Reconnect protocol** | Re-send subscribe + history_push after reconnect | Backend |
| **New error codes** | Handle `PAYLOAD_TOO_LARGE`, `CHUNK_SEQUENCE_ERROR` | Backend + Agents |
| **Performance requirements** | Optimize for <100ms per chunk, O(n) algorithms | Indicator agents |

---

## Migration Steps

### 1. Update Metadata Endpoint (All Agents)

**Add `transport_limits` to `/metadata` response:**

```json
{
  "spec_version": "ACP-0.4.0",
  "agent_id": "sma_indicator",
  "agent_name": "Simple Moving Average",
  "agent_version": "1.0.0",
  "agent_type": "indicator",
  "transport_limits": {
    "max_records_per_chunk": 5000,
    "max_websocket_message_bytes": 10485760,
    "chunk_timeout_seconds": 30
  },
  "indicators": [ /* ... */ ],
  "outputs": [ /* ... */ ]
}
```

**Field Requirements:**
- `max_records_per_chunk`: Integer between 1000-10000. Recommended: **5000**.
- `max_websocket_message_bytes`: Minimum 1MB (1048576). Recommended: **10MB (10485760)**.
- `chunk_timeout_seconds`: Optional, default 30.

**Implementation Notes:**
- Set `max_records_per_chunk` based on your agent's memory and processing capacity
- For 3-year data (1.5M records) with 5000-record chunks = 300 chunks total
- Lower values = more chunks but smaller memory footprint
- Higher values = fewer chunks but larger memory spikes

---

### 2. Implement Chunked History Push (Backend)

Backend must split large `history_push` payloads into chunks respecting agent's `max_records_per_chunk`.

**Before (ACP v0.3.0):**
```python
# Send all 12000 candles at once - BREAKS at scale!
await websocket.send_json({
    "type": "history_push",
    "spec_version": "ACP-0.3.0",
    "session_id": session_id,
    "subscription_id": sub_id,
    "agent_id": agent_id,
    "symbol": "SPY",
    "interval": "1m",
    "candles": all_candles,  # 12000 records
    "count": 12000
})
```

**After (ACP v0.4.0):**
```python
max_per_chunk = agent_metadata["transport_limits"]["max_records_per_chunk"]
total_chunks = math.ceil(len(all_candles) / max_per_chunk)

for chunk_idx in range(total_chunks):
    start = chunk_idx * max_per_chunk
    end = min(start + max_per_chunk, len(all_candles))
    chunk_candles = all_candles[start:end]
    
    await websocket.send_json({
        "type": "history_push",
        "spec_version": "ACP-0.4.0",
        "session_id": session_id,
        "subscription_id": sub_id,
        "agent_id": agent_id,
        "symbol": "SPY",
        "interval": "1m",
        "candles": chunk_candles,
        "count": len(chunk_candles),
        "chunk_index": chunk_idx,
        "total_chunks": total_chunks,
        "is_final_chunk": (chunk_idx == total_chunks - 1)
    })
    
    # Optional: log progress
    logger.info(f"Sent chunk {chunk_idx + 1}/{total_chunks} ({len(chunk_candles)} candles)")
```

**Key Points:**
- Send chunks sequentially (don't wait for agent response between chunks)
- Monotonic `chunk_index` starting at 0
- Set `is_final_chunk: true` only on last chunk
- Don't add artificial delays between chunks

---

### 3. Implement Chunked History Receive (Indicator Agents)

Agents must accumulate chunks until `is_final_chunk=true`, then process.

**Before (ACP v0.3.0):**
```python
if msg_type == "history_push":
    message = HistoryPushMessage.model_validate(incoming)
    series = session_store.upsert_history(
        message.session_id, message.symbol, message.interval, message.candles
    )
    response = overlay_engine.history_response(sub, series, agent_id)
    await websocket.send_json(response.model_dump())
```

**After (ACP v0.4.0):**
```python
# Add chunk accumulator per subscription
chunk_accumulators: dict[tuple[str, str], list[OHLCRecord]] = {}

if msg_type == "history_push":
    message = HistoryPushMessage.model_validate(incoming)
    key = (message.session_id, message.subscription_id)
    
    # Validate chunk sequence
    if message.chunk_index is not None:
        if message.chunk_index == 0:
            chunk_accumulators[key] = []
        
        expected_idx = len(chunk_accumulators.get(key, [])) // max_records_per_chunk
        if message.chunk_index != expected_idx:
            await send_error(websocket, ErrorMessage(
                session_id=message.session_id,
                subscription_id=message.subscription_id,
                agent_id=agent_id,
                code="CHUNK_SEQUENCE_ERROR",
                message=f"Expected chunk {expected_idx}, got {message.chunk_index}",
                retryable=False
            ))
            continue
        
        # Accumulate this chunk
        chunk_accumulators[key].extend(message.candles)
        
        # If not final, acknowledge and wait for more
        if not message.is_final_chunk:
            logger.info(f"Accumulated chunk {message.chunk_index + 1}/{message.total_chunks}")
            continue
        
        # Final chunk received - process accumulated candles
        all_candles = chunk_accumulators.pop(key)
        logger.info(f"Received all {len(all_candles)} candles across {message.total_chunks} chunks")
    else:
        # Single-chunk legacy compatibility (optional)
        all_candles = message.candles
    
    # Upsert accumulated candles
    series = session_store.upsert_history(
        message.session_id, message.symbol, message.interval, all_candles
    )
    
    # Compute overlays (may also be chunked)
    overlays = compute_sma_series(series.ordered(), period, prefix=sub.subscription_id)
    
    # Send chunked history_response
    await send_chunked_history_response(websocket, overlays, sub, agent_id)
```

**Key Points:**
- Use dict/map to track chunks per subscription
- Validate `chunk_index` sequence (must be monotonic)
- Only process after `is_final_chunk=true`
- Clear accumulator after processing

---

### 4. Implement Chunked History Response (Indicator Agents)

When computed overlays exceed `max_records_per_chunk`, send in chunks.

**Helper Function:**
```python
async def send_chunked_history_response(
    websocket: WebSocket,
    overlays: list[LineRecord],
    sub: Subscription,
    agent_id: str,
    max_per_chunk: int = 5000
):
    total_chunks = math.ceil(len(overlays) / max_per_chunk)
    
    for chunk_idx in range(total_chunks):
        start = chunk_idx * max_per_chunk
        end = min(start + max_per_chunk, len(overlays))
        chunk_overlays = overlays[start:end]
        
        response = HistoryResponseMessage(
            session_id=sub.session_id,
            subscription_id=sub.subscription_id,
            agent_id=agent_id,
            schema="line",
            overlays=chunk_overlays,
            metadata={
                "indicator": sub.indicator_id,
                "period": sub.period,
                "count": len(overlays),  # Total count, not chunk count
            },
            chunk_index=chunk_idx,
            total_chunks=total_chunks,
            is_final_chunk=(chunk_idx == total_chunks - 1)
        )
        
        await websocket.send_json(response.model_dump(by_alias=True))
        logger.info(f"Sent history_response chunk {chunk_idx + 1}/{total_chunks}")
```

---

### 5. Implement Chunked History Response Receive (Backend)

Backend must accumulate agent `history_response` chunks before forwarding to UI.

**Implementation:**
```python
# Add response accumulator per subscription
response_accumulators: dict[tuple[str, str], list[dict]] = {}

if msg_type == "history_response":
    message = HistoryResponseMessage.model_validate(incoming)
    key = (message.session_id, message.subscription_id)
    
    if message.chunk_index is not None:
        if message.chunk_index == 0:
            response_accumulators[key] = []
        
        # Accumulate overlays from this chunk
        response_accumulators[key].extend(message.overlays)
        
        if not message.is_final_chunk:
            logger.info(f"Accumulated response chunk {message.chunk_index + 1}/{message.total_chunks}")
            continue
        
        # Final chunk - combine and forward
        all_overlays = response_accumulators.pop(key)
        message.overlays = all_overlays
        logger.info(f"Received all {len(all_overlays)} overlays from agent")
    
    # Forward complete response to UI clients
    await forward_to_ui_clients(session_id, message)
```

---

### 6. Implement Reconnect Protocol (Backend)

After WebSocket reconnect, backend MUST re-bootstrap subscriptions.

**Before (ACP v0.3.0):**
```python
# On reconnect, resume sending tick_updates immediately
# PROBLEM: Agent has no history context!
```

**After (ACP v0.4.0):**
```python
class BackendSession:
    def __init__(self):
        self.active_subscriptions: dict[str, Subscription] = {}
        self.websocket_connected = False
    
    async def on_websocket_reconnect(self, websocket: WebSocket):
        self.websocket_connected = True
        logger.info(f"WebSocket reconnected, re-bootstrapping {len(self.active_subscriptions)} subscriptions")
        
        # Re-bootstrap each subscription
        for sub_id, sub in self.active_subscriptions.items():
            # 1. Send subscribe message
            await websocket.send_json({
                "type": "subscribe",
                "spec_version": "ACP-0.4.0",
                "session_id": sub.session_id,
                "subscription_id": sub_id,
                "agent_id": sub.agent_id,
                "symbol": sub.symbol,
                "interval": sub.interval,
                "indicator_id": sub.indicator_id,
                "params": sub.params
            })
            
            # 2. Send full chunked history_push
            canonical_candles = self.get_canonical_candles(sub.symbol, sub.interval)
            await send_chunked_history_push(websocket, sub, canonical_candles)
            
            logger.info(f"Re-bootstrapped subscription {sub_id}")
        
        # 3. Resume normal tick_update flow
```

**Key Points:**
- Always re-send `subscribe` + full `history_push` after reconnect
- Don't assume agent remembers any prior state
- Wait for `history_response` before sending new `tick_update` messages

---

### 7. Update Error Handling

Add handlers for new error codes.

**New Error Codes:**
```python
# Sent when message exceeds max_websocket_message_bytes
ErrorMessage(
    code="PAYLOAD_TOO_LARGE",
    message="Message size 15MB exceeds limit 10MB",
    retryable=False
)

# Sent when chunks arrive out of order
ErrorMessage(
    code="CHUNK_SEQUENCE_ERROR",
    message="Expected chunk 2, received chunk 4",
    retryable=False
)
```

**Backend Handling:**
```python
if error.code == "PAYLOAD_TOO_LARGE":
    # Reduce max_records_per_chunk and retry
    agent_metadata["transport_limits"]["max_records_per_chunk"] = 2500
    await retry_subscription(sub)

elif error.code == "CHUNK_SEQUENCE_ERROR":
    # Clear state and restart history push
    await unsubscribe(sub)
    await subscribe_and_bootstrap(sub)
```

---

### 8. Performance Optimization (Indicator Agents)

Ensure agents meet ACP v0.4.0 performance requirements.

**Requirements:**
- <100ms per chunk processing (upsert + compute)
- O(n) or O(n log n) algorithms
- Async yields every 1000 records
- Memory efficient (<100 bytes per candle)

**Example Optimization:**
```python
async def upsert_batch_async(self, candles: list[OHLCRecord]) -> None:
    """Upsert with async yields for large batches."""
    for i, candle in enumerate(candles):
        self.candles_by_id[candle.id] = candle
        
        # Yield to event loop every 1000 records
        if i % 1000 == 0 and i > 0:
            await asyncio.sleep(0)
    
    # Sort once at end (O(n log n))
    self.ordered_ids = sorted(self.candles_by_id.keys(), 
                              key=lambda cid: self.candles_by_id[cid].ts)
```

**Diagnostic Logging:**
```python
start = time.monotonic()
series.upsert_batch(candles)
elapsed_ms = (time.monotonic() - start) * 1000

if elapsed_ms > 100:
    logger.warning(f"Chunk processing exceeded 100ms budget: {elapsed_ms:.2f}ms for {len(candles)} candles")
```

---

## Testing Checklist

### Unit Tests
- [ ] Metadata includes `transport_limits` with valid ranges
- [ ] Chunking logic splits arrays correctly at boundaries
- [ ] Chunk sequence validation rejects out-of-order chunks
- [ ] Single-chunk messages work (backward compatibility)
- [ ] Error codes serialize correctly

### Integration Tests
- [ ] 5,000 candles: single chunk (baseline)
- [ ] 10,000 candles: 2 chunks
- [ ] 25,000 candles: 5 chunks
- [ ] 100,000 candles: 20 chunks
- [ ] 1,500,000 candles: 300 chunks (3-year test)

### Reconnect Tests
- [ ] Simulate WebSocket disconnect after chunk 50 of 100
- [ ] Verify backend re-sends subscribe + full history
- [ ] Verify agent clears stale chunk accumulator
- [ ] Verify UI receives complete overlays after reconnect

### Performance Tests
- [ ] Measure per-chunk latency (target: <100ms)
- [ ] Measure full 1.5M candle backfill time (target: <60s)
- [ ] Monitor memory usage (target: <150MB for 1.5M candles)
- [ ] Verify CTRL+C responsiveness during large loads

### Error Handling Tests
- [ ] Simulate oversized message → `PAYLOAD_TOO_LARGE`
- [ ] Send chunks out of order → `CHUNK_SEQUENCE_ERROR`
- [ ] Timeout between chunks → agent resets state
- [ ] Backend recovers gracefully from agent errors

---

## Common Pitfalls

### 1. Forgetting to Update spec_version
**Symptom:** Messages rejected with version mismatch error  
**Fix:** Update all message `spec_version` fields to `"ACP-0.4.0"`

### 2. Not Implementing Chunk Accumulation
**Symptom:** Indicator computes on partial data, sends incorrect overlays  
**Fix:** Buffer chunks until `is_final_chunk=true` before processing

### 3. Backend Waits for Agent Response Between Chunks
**Symptom:** Slow backfill, chunks sent serially instead of pipelined  
**Fix:** Send all chunks immediately without waiting for acknowledgment

### 4. Not Re-Bootstrapping After Reconnect
**Symptom:** After disconnect, SMA line disappears permanently  
**Fix:** Backend must re-send `subscribe` + `history_push` on every reconnect

### 5. Using O(n²) Algorithms
**Symptom:** Per-chunk latency exceeds 100ms, backfill takes >5 minutes  
**Fix:** Use batch upsert with deferred sort (O(n log n)), not per-item insert

### 6. Missing Async Yields
**Symptom:** CTRL+C doesn't work during large backfill  
**Fix:** Add `await asyncio.sleep(0)` every 1000 records

---

## Rollout Strategy

### Phase 1: Update Agents (Week 1)
1. Update indicator agents to v0.4.0
2. Add `transport_limits` to metadata
3. Implement chunk receive logic
4. Deploy to staging
5. Test with 100K+ candles

### Phase 2: Update Backend (Week 2)
1. Update backend to v0.4.0
2. Implement chunked `history_push` send
3. Implement chunked `history_response` receive
4. Add reconnect bootstrap logic
5. Deploy to staging
6. Test full integration

### Phase 3: Production Rollout (Week 3)
1. Blue/green deploy backend
2. Monitor logs for `PAYLOAD_TOO_LARGE` errors
3. Verify 1-month and 3-year loads work without disconnect
4. Monitor performance metrics (<60s for 3-year backfill)
5. Update documentation

### Phase 4: Cleanup (Week 4)
1. Remove any ACP v0.3.0 compatibility shims
2. Archive old protocol docs
3. Update examples and tutorials

---

## Support and References

- **ACP v0.4.0 Spec:** `/protocols/spec/ACP.md`
- **Message Schema:** `/protocols/schemas/messages.json`
- **Example Agent:** `/protocols/examples/basic_test_agent/`
- **Performance Benchmarks:** `/PERFORMANCE_IMPROVEMENTS.md`

For questions or issues during migration, consult the protocol spec or run the conformance test suite in `/protocols/examples/`.

---

## Summary

ACP v0.4.0 solves the "message too big" failure by mandating chunked delivery for large datasets. All participants (backend, price agents, indicator agents) must implement:

1. **Transport limits in metadata** - Declare `max_records_per_chunk`
2. **Chunked send** - Split payloads exceeding limits
3. **Chunked receive** - Accumulate chunks before processing
4. **Reconnect bootstrap** - Re-send full history after disconnect
5. **Performance optimization** - <100ms per chunk, O(n) algorithms

**Migration effort:** 4-8 hours per component  
**Performance improvement:** 3-year backfill in <60 seconds  
**Reliability improvement:** Zero disconnects due to message size

This is a required migration. All ACP v0.3.0 systems will experience failures with 7,500+ candles.
