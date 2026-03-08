**AI-Powered Trading Platform**

Architecture & Implementation Brief

*Prepared for: Continued Development with AI Assistant*

# **1\. Project Vision**

A personal stock trading platform modeled after TradingView, extended with multiple AI and algorithmic agents that add contextual overlay data to price charts. The platform unifies disparate data sources — news sentiment, technical indicators, options flow, pattern recognition — into a single coherent chart view.

The broker is Charles Schwab. Their API provides REST endpoints for historical candle data (limited to \~40 days) and a WebSocket for live tick and closed candle streaming.

# **2\. Architecture Overview**

## **2.1 Core Principle**

The backend (‘Odin’) is the single source of truth for candle data within a user session. It owns the canonical candle set and distributes it to agents. This guarantees every agent computes against identical data, eliminating consistency bugs.

## **2.2 High-Level Stack**

| Frontend | React app, connects to backend via WebSocket |
| :---- | :---- |
| **Backend** | Python, FastAPI, asyncio — owns sessions and candle state |
| **Price Agent** | Ingests Schwab broker data, serves historical candles, manages candle lifecycle |
| **Indicator Agent** | Stateful microservice — EMA, SMA, RSI, KAMA etc. Multiple indicators share one candle store per session |
| **News Agent** | Maintains own database, serves sentiment markers for historical range and live events |
| **Pattern Agent** | Stateful — support/resistance, chart pattern detection computed from candle data |
| **Options Agent** | Tracks Delta/Theta exposure, serves as chart overlay |
| **Protocol** | Custom WebSocket message protocol shared by all agents |

## **2.3 Data Flow**

Schwab Broker API (REST \+ WebSocket)

        |

  \[Price Agent\]

  \- Manages candle lifecycle: PARTIAL \-\> PROVISIONAL_CLOSE \-\> SESSION_RECONCILED \-\> FINAL

  \- Persists candles to DB (long-term store)

  \- Serves GET /history to backend on session start

  \- Streams live candle events to backend via WebSocket

        |

   \[Backend — Session Manager\]

   \- Owns canonical candle array per session

   \- Pushes history \+ live events to all agents except price agent

   \- Merges overlay responses from agents

   \- Sends unified updates to frontend

        |

  \[Agent WebSocket Connections — per session\]

   EMA Agent | RSI Agent | News Agent | Pattern Agent

        |

   \[React Frontend\]

   \- Price layer updates immediately on new candle

   \- Overlay layers update asynchronously as agents respond

# **3\. Session Model**

The backend supports multiple simultaneous frontend windows, each representing an independent session with its own ticker, interval, time range, and agent configuration.

## **3.1 Session Object**

Session {

  session\_id: str          \# unique per frontend window

  ticker: str              \# e.g. 'SPY'

  interval: str            \# e.g. '1min'

  range: str               \# e.g. '7day', '30day'

  candles: List\[Candle\]    \# canonical candle array — backend owns this

  agents: List\[AgentConn\]  \# active agent WebSocket connections

  seq: int                 \# incrementing sequence counter

  state: INITIALIZING | READY

}

## **3.2 Session Lifecycle**

1. Frontend connects to backend WebSocket, sends session config (ticker, interval, range, agent list)

2. Backend fetches historical candles from Price Agent GET /history

3. Backend stores candles as canonical set for this session

4. Backend opens WebSocket to each registered agent, sends HISTORY\_PUSH with full candle set and params

5. Each agent loads history into memory, computes initial overlay values, sends HISTORY\_RESPONSE back

6. Backend merges candles \+ all overlay responses, sends full initial payload to frontend

7. Session enters READY state — live streaming begins

# **4\. The Agent Protocol**

All agents must implement a common WebSocket message protocol. The backend is agent-agnostic — it only speaks the protocol. Adding a new agent requires zero backend code changes.

## **4.1 Message Envelope**

{

  "msg\_type": "...",

  "session\_id": "abc123",

  "sub\_id": "ema\_9",          \# subscription within session

  "ticker": "SPY",

  "interval": "1min",

  "seq": 10042,               \# incrementing, gap \= missed message

  "timestamp": "2024-01-15T15:16:00Z",

  "payload": { ... }

}

## **4.2 Backend \-\> Agent Message Types**

| msg\_type | Direction | Purpose |
| :---- | :---- | :---- |
| HISTORY\_PUSH | Backend \-\> Agent | Full candle array \+ params on session start |
| TICK\_UPDATE | Backend \-\> Agent | Live quote update, candle still forming (final: false) |
| CANDLE\_CLOSED | Backend \-\> Agent | A candle has officially closed |
| CANDLE\_CORRECTION | Backend \-\> Agent | Broker reconciliation changed a past candle |
| RESYNC\_RESPONSE | Backend \-\> Agent | Replay of recent messages after gap detected |

## **4.3 Agent \-\> Backend Message Types**

| msg\_type | Direction | Purpose |
| :---- | :---- | :---- |
| HISTORY\_RESPONSE | Agent \-\> Backend | Computed overlay for full historical range |
| OVERLAY\_UPDATE | Agent \-\> Backend | New computed value after tick or candle close |
| OVERLAY\_MARKER | Agent \-\> Backend | Discrete event marker (signal, news hit, pattern) |
| RESYNC\_REQUEST | Agent \-\> Backend | Gap detected in seq — request replay |
| ERROR | Agent \-\> Backend | Computation or data error |

## **4.4 Sequence Gap Handling**

Every participant tracks last\_seq\_received. On each inbound message:

if msg.seq \!= last\_seq\_received \+ 1:

    send RESYNC\_REQUEST(last\_seq\_received=last\_seq\_received)

\# Backend holds rolling buffer of last \~100 messages per session

\# RESYNC\_RESPONSE replays from last known good seq

\# If gap too large: falls back to full HISTORY\_PUSH

## **4.5 Overlay Marker Shape**

{

  "msg\_type": "OVERLAY\_MARKER",

  "sub\_id": "news\_sentiment",

  "payload": {

    "type": "SIGNAL",

    "timestamp": "2024-01-15T15:15:00Z",

    "direction": "bullish",

    "label": "Positive earnings headline",

    "color": "green",

    "shape": "triangle\_up",

    "position": "above\_candle"

  }

}

# **5\. The Price Agent**

The Price Agent is the system's interface to the Schwab broker API. It is the only component that communicates with Schwab directly.

## **5.1 Candle Lifecycle**

A candle at a given timestamp passes through multiple states as the broker reconciles data:

| PARTIAL | Candle is forming. Updated every tick. Not yet closed. |
| :---- | :---- |
| **PROVISIONAL_CLOSE** | Broker streaming WebSocket says the minute closed. May differ from REST history. |
| **SESSION_RECONCILED** | REST history API has confirmed/reconciled this timestamp. May be same or changed versus stream close. |
| **FINAL** | EOD and T+1 reconciliation complete. Value is stable. |

*Candles are stored with their state field. When reconciliation changes OHLCV values, the candle is updated in place (same timestamp, same row). A CANDLE\_CORRECTION event is broadcast to all active sessions for that ticker.*

## **5.2 Responsibilities**

* Maintain WebSocket connection to Schwab streaming API

* Aggregate tick data into candles at requested intervals

* Persist every closed candle to DB with state tracking

* Run background reconciliation poll against Schwab REST API every N minutes

* Serve GET /history — returns candles at their current best state for a given ticker/interval/range

* Stream live events to backend sessions subscribed to a given ticker

## **5.3 Storage**

DB is used for candle persistence. It is purpose-built for time-series data

candles table:

  ticker       VARCHAR

  interval     VARCHAR

  timestamp    TIMESTAMPTZ   \-- candle identity, unique per ticker+interval

  open         NUMERIC

  high         NUMERIC

  low          NUMERIC

  close        NUMERIC

  volume       BIGINT

  state        ENUM(PARTIAL, PROVISIONAL_CLOSE, SESSION_RECONCILED, FINAL)

  updated\_at   TIMESTAMPTZ

# **6\. The Indicator Agent**

The Indicator Agent is a single Python process that supports multiple indicator types (EMA, SMA, RSI, KAMA, etc.) and manages multiple concurrent sessions. It is stateful — each session's candle data is held in memory.

## **6.1 Internal Architecture**

IndicatorAgent

  sessions: Dict\[session\_id, SessionStore\]

    SessionStore

      candles: List\[Candle\]        \# shared across all subs in this session

      subscriptions: Dict\[sub\_id, IndicatorInstance\]

        EMA(period=9)              \# own running state, O(1) per tick

        EMA(period=21)             \# own running state

        RSI(period=14)             \# own running state

One candle copy per session regardless of how many indicators are active. Adding a new indicator type is a new Python class only — no protocol or backend changes.

## **6.2 Incremental Computation**

Indicators maintain running state so each tick requires O(1) computation, not a full series recalculation:

class EMA:

    def \_\_init\_\_(self, period):

        self.period \= period

        self.multiplier \= 2 / (period \+ 1\)   \# precomputed once

        self.last\_ema \= None

    def on\_tick(self, candle) \-\> float:

        if self.last\_ema is None:

            self.last\_ema \= candle.close     \# seed

            return self.last\_ema

        self.last\_ema \= (candle.close \* self.multiplier) \+ \\

                        (self.last\_ema \* (1 \- self.multiplier))

        return self.last\_ema

History walk happens once at session init (milliseconds). Thereafter each tick is 2-3 arithmetic operations.

## **6.3 Initialization Guard**

Ticks may arrive before the history walk completes. The agent buffers incoming ticks during INITIALIZING state and replays them once history computation is done before entering READY state.

## **6.4 Performance at Scale**

Example load: 3 tickers, 14 indicators, 30-day 1-minute dataset, 1-second tick updates.

| Candle dataset per session | \~11,700 candles (30d x 390 min/day) |
| :---- | :---- |
| **Updates per second** | \~14 indicator computations (trivial arithmetic) |
| **History init time** | Milliseconds per session |
| **Memory per session** | \~11,700 candles x 3 sessions \= \~35,000 candles total |
| **Python async capacity** | Thousands of WebSocket messages/sec — not the bottleneck |

# **7\. Multi-Window Support**

Multiple frontend windows are supported by one backend instance. Each window is an independent session. The backend, each agent, and the price agent all namespace state by session\_id.

Example: Two windows using the same EMA agent with different parameters create two sub\_id subscriptions within the same agent process. They share no state and cannot interfere with each other.

Window A: SPY | 1min | 7day | EMA(9)   \-\> session\_abc, sub\_id: ema\_9

Window B: QQQ | 1min | 7day | EMA(14)  \-\> session\_def, sub\_id: ema\_14

EMA Agent memory:

  session\_abc: { candles: \[SPY candles\], sub: EMA(9) }

  session\_def: { candles: \[QQQ candles\], sub: EMA(14) }

# **8\. Frontend Rendering Model**

The React frontend treats price data and overlay data as independent layers:

* Price layer — updates immediately when a new candle or tick arrives. Never waits for agents.

* Overlay layers — update asynchronously as each agent responds. Each overlay is identified by sub\_id and renders independently.

* Markers (signals, news, patterns) — discrete events pinned to a candle timestamp, rendered as shapes above/below candles.

The frontend connects to the backend via a single WebSocket. The backend multiplexes all session data (price \+ all overlays) over this connection.

# **9\. Technology Stack**

| Component | Technology | Notes |
| :---- | :---- | :---- |
| Frontend | React | TradingView-style chart, WebSocket to backend |
| Backend | Python, FastAPI, asyncio | Session manager, protocol orchestrator |
| Price Agent | Python, FastAPI | Schwab API integration, candle lifecycle |
| Indicator Agent | Python | EMA, SMA, RSI, KAMA — stateful, session-aware |
| News Agent | Python | Own DB, external news API integration |
| Long-term storage | DB | Candle history, news events, corrections |
| Broker API | Charles Schwab | REST for history (\~40 day limit), WebSocket for live |

# **10\. Recommended Build Order**

8. Price Agent — Schwab WebSocket ingest, candle lifecycle, DB writes, GET /history endpoint

9. Backend session manager — session object, canonical candle store, agent registry, frontend WebSocket

10. Protocol implementation — message envelope, seq tracking, resync logic (shared library)

11. EMA Indicator Agent — simplest stateful agent, validates full pipeline end to end

12. React frontend — chart rendering, WebSocket connection, price \+ overlay layer model

13. Additional indicators — SMA, RSI, KAMA added as classes to Indicator Agent

14. News Agent, Pattern Agent, Options Agent — after core pipeline is stable

# **11\. Key Design Decisions (Summary)**

| Backend owns candles | All agents receive identical candle sets — eliminates consistency bugs between indicators and price chart |
| :---- | :---- |
| **Agents are stateful** | History loaded once at session start. Ticks are incremental O(1) updates. Passing 11,700 candles every second is not acceptable. |
| **Protocol not REST** | Agents communicate via WebSocket protocol, not REST polling. Bidirectional — backend pushes candles, agents push overlays back on same connection. |
| **One indicator process** | Multiple indicator types and multiple sessions handled by one Python process. Candle set shared per session across all indicators. |
| **Candle corrections** | Schwab reconciles candle data post-close and EOD. Price Agent tracks state (PARTIAL/PROVISIONAL_CLOSE/SESSION_RECONCILED/FINAL) and broadcasts typed CANDLE\_CORRECTION events. |
| **Price renders first** | Frontend renders price immediately on new candle. Overlays update asynchronously. Chart never lags waiting for agent computation. |
| **Session isolation** | Multiple frontend windows fully isolated by session\_id. Different tickers, intervals, and agent configs per window with no interference. |

*End of Brief*