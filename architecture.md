# Odin – Architecture & Tech Stack Overview

Odin is a personal trading platform inspired by TradingView and ThinkorSwim, but extended to support real-time AI-driven overlays (news, sentiment, patterns, regime detection, option exposure, etc.) in addition to standard streaming market data.  My goal is to have a modern, responsive, charting application that can blend external timestamped data with the standard OHLCV candle data into a cohesive and useful platform.

This document defines the architecture, tech stack, and design principles for the codebase.

---

## 🎯 Goals

- Real-time candlestick charting
- Streaming stock quote data
- Streaming AI overlay data (sentiment, news, pattern detection)
- Unified timeline of price + AI signals
- Fast, responsive UI
- Modular agent integration
- Extensible indicator system
- Possible addition of trading bots that can use the combined platform data to make trade decisions automatically

---

Backend acts as a **traffic cop**:
- Subscribes to broker streams
- Subscribes to AI agent streams
- Normalizes data
- Timestamps events
- Merges streams
- Pushes unified events to frontend

Frontend:
- Renders candles
- Renders overlays
- Manages user interaction

---

## 🧰 Tech Stack

### Backend
- Language: **Python**
- Framework: **FastAPI**
- Concurrency: `asyncio`
- Transport:
  - WebSockets for live streams
  - REST for historical data
- Role:
  - Pub/sub router
  - Event normalizer
  - Stream merger
  - Agent gateway
- No AI inference runs directly in backend

### Frontend
- Language: **TypeScript / JavaScript**
- Framework: **React** (or Svelte is acceptable)
- Charting: **TradingView Lightweight Charts**
- Transport: WebSocket client
- UI: Tailwind or similar

### Agents
- External services
- Publish overlay events to backend
- Provide REST endpoints for historical data
- Examples:
  - News sentiment
  - Pattern detection
  - Regime classification
  - Option exposure analysis

---

🧠 Core Insight
This system is more powerful than PineScript-style platforms because it supports:
External data
AI agents
Semantic overlays
Multi-source fusion
The chart is not just price — it is a unified timeline of:
Market behavior
Machine interpretation
Narrative signals

🏁 Initial Build Order
Repo skeleton
Shared protocol schema
Backend WebSocket server
Frontend chart rendering
Price stream
Overlay stream
indicators
Agent integration
