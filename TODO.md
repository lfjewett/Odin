# Trade Manager Phase 0 MVP TODO

## Decisions locked
- Strategy results transport: API apply response + chart markers rendered client-side
- Persistence: strategy definitions only
- Runtime: backend-only evaluation
- Scope: one active strategy per session

## Backend
- [x] Add persistent trade strategy store (`session_id` + `strategy_name` key)
- [x] Add strategy CRUD endpoints
  - [x] `GET /api/sessions/{session_id}/trade-strategies`
  - [x] `GET /api/sessions/{session_id}/trade-strategies/{strategy_name}`
  - [x] `PUT /api/sessions/{session_id}/trade-strategies/{strategy_name}`
  - [x] `DELETE /api/sessions/{session_id}/trade-strategies/{strategy_name}`
- [x] Add syntax validation endpoint
  - [x] `POST /api/sessions/{session_id}/trade-strategies/validate`
- [x] Add apply endpoint
  - [x] `POST /api/sessions/{session_id}/trade-strategies/apply`
- [x] Implement minimal DSL parser + validator
  - [x] `A > B`
  - [x] `A < B`
  - [x] `IN_BULL_TRADE`
  - [x] `!IN_BULL_TRADE`
  - [x] `AND`, `OR`, parentheses
- [x] Implement trade engine left-to-right evaluation on session candles
- [x] Emit ENTRY/EXIT markers in apply response

## Frontend
- [x] Add strategy API client methods
- [x] Upgrade Trade Manager modal
  - [x] Load saved strategy
  - [x] Save strategy
  - [x] Delete strategy
  - [x] Edit entry/exit rules
  - [x] Validate syntax button
  - [x] Apply button closes modal and returns to chart
- [x] Add chart marker state in app shell
- [x] Render ENTRY/EXIT markers on chart series
- [x] Increment app title version string (`v0.50` -> `v0.51`)

## Remaining before merge
- [x] Run backend start + API smoke tests for strategy endpoints
- [x] Run frontend typecheck/build
- [x] Manual E2E pass:
  - [x] Open Trade Manager
  - [x] Save strategy
  - [x] Reload strategy
  - [x] Validate strategy
  - [x] Apply strategy
  - [x] Confirm ENTRY/EXIT markers display on chart
- [x] Document DSL examples in docs (phase-0 quick reference)


Strategy:
SMA-20:SMA > SMA-50:SMA AND CLOSE < SMA-20:SMA AND !IN_BULL_TRADE
SMA-20:SMA < SMA-50:SMA AND IN_BULL_TRADE

long_entry: TIME > 0930 AND TIME < 1550 AND FAST:KAMA > SLOW:KAMA AND LOW < SLOW:KAMA AND !IN_BULL_TRADE AND (LONG_ENTRIES_SINCE((FAST:KAMA > SLOW:KAMA AND FAST:KAMA[1] <= SLOW:KAMA[1])) == 0)

long_entry: TIME > 0930 AND TIME < 1550 (LONG_ENTRIES_SINCE((FAST:KAMA > SLOW:KAMA AND FAST:KAMA[1] <= SLOW:KAMA[1])) == 0)
long_exit_1: IN_BULL_TRADE AND CLOSE > ( ENTRY_PRICE + ( ATR:ATR * 1.5 ))
long_exit_2: IN_BULL_TRADE AND FAST:KAMA < SLOW:KAMA AND HIGH > FAST:KAMA
long_exit_3: IN_BULL_TRADE AND CLOSE < ENTRY_PRICE - (ATR:ATR * 4)
long_exit_4: IN_BULL_TRADE AND TIME == 1555

short_entry: TIME > 0930 and TIME < 1550 and FAST:KAMA < SLOW:KAMA and HIGH > SLOW:KAMA and !IN_BEAR_TRADE AND (SHORT_ENTRIES_SINCE(CROSSES_BELOW(FAST:KAMA, SLOW:KAMA)) == 0)
short_exit_1: IN_BEAR_TRADE and CLOSE < ( ENTRY_PRICE - ( ATR:ATR * 1.5))
short_exit_2: IN_BEAR_TRADE and FAST:KAMA > SLOW:KAMA AND LOW < FAST:KAMA
short_exit_3: IN_BEAR_TRADE and CLOSE > ENTRY_PRICE + (ATR:ATR * 4)
short_exit_4: IN_BEAR_TRADE and TIME == 1555  