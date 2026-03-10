# Trade Manager Phase 1 DSL & API

## Scope (Phase 1)

The DSL has been significantly enhanced from Phase 0 to support production-ready trading strategies:

### Comparison Operators
- `>`, `<`, `>=`, `<=`, `==`, `!=`

### Boolean Operators
- `AND`, `OR`, `NOT` (or `!`)

### Arithmetic Operators
- `+`, `-`, `*`, `/`, `%` (modulo)
- Full operator precedence: multiplication/division before addition/subtraction
- Parentheses for grouping: `(HIGH + LOW) / 2`

### Functions
- `MIN(a, b, ...)` - Returns minimum of arguments (variadic)
- `MAX(a, b, ...)` - Returns maximum of arguments (variadic)
- `ABS(x)` - Returns absolute value
- `CROSSES_ABOVE(a, b)` - Returns 1 when `a` crosses above `b` (current: a > b, previous: a <= b)
- `CROSSES_BELOW(a, b)` - Returns 1 when `a` crosses below `b` (current: a < b, previous: a >= b)
- `WITHIN(condition, bars)` - Returns true if condition was true within last N bars
- `BARS_SINCE(condition)` - Returns number of bars since condition was last true (0 if true now)

### Lookback References
- Array-style indexing: `CLOSE[1]` = previous bar, `CLOSE[2]` = 2 bars ago
- Works with any variable: `SMA-20:SMA[1]`, `HIGH[3]`
- Maximum lookback: 200 bars (configurable)

### Time Filtering
- `TIME` - Integer in HHMM format (e.g., 1030, 1545) - Eastern Time
- `HOUR` - Integer 0-23 (Eastern Time)
- `MINUTE` - Integer 0-59 (Eastern Time)
- Example: `TIME > 1030 AND TIME < 1400` (only trade between 10:30 AM and 2:00 PM ET)

### Position State
- `IN_BULL_TRADE` - True when in a long position
- `IN_BEAR_TRADE` - True when in a short position (infrastructure in place)
- `!IN_BULL_TRADE` - True when not in a long position
- `!IN_BEAR_TRADE` - True when not in a short position

### Built-in Candle Variables
- `BODY` - Absolute value of (CLOSE - OPEN)
- `RANGE` - HIGH - LOW
- `UPPER_WICK` - HIGH - MAX(OPEN, CLOSE)
- `LOWER_WICK` - MIN(OPEN, CLOSE) - LOW

The runtime walks candles left-to-right and emits `LONG_ENTRY`/`LONG_EXIT` and `SHORT_ENTRY`/`SHORT_EXIT` markers based on state transitions.

## Available Variables

Base variables (always available):

### OHLCV Data
- `OPEN`
- `HIGH`
- `LOW`
- `CLOSE`
- `VOLUME`

### Built-in Candle Metrics
- `BODY` - Absolute value of (CLOSE - OPEN)
- `RANGE` - HIGH - LOW
- `UPPER_WICK` - HIGH - MAX(OPEN, CLOSE)
- `LOWER_WICK` - MIN(OPEN, CLOSE) - LOW

### Time Variables (Eastern Time)
- `TIME` - Integer HHMM format (e.g., 1030 = 10:30 AM, 1545 = 3:45 PM)
- `HOUR` - Integer 0-23
- `MINUTE` - Integer 0-59

### Position State
- `IN_BULL_TRADE` - Boolean, true when in a long position
- `IN_BEAR_TRADE` - Boolean, true when in a short position (infrastructure in place)

Indicator variables come from `/api/sessions/{session_id}/variables`.

**Variable Name Format**: Indicator variables follow the pattern `AgentName:Label` or `AgentName:Label:Field` for multi-output indicators. Variable names can contain:
- Letters (A-Z, a-z)
- Digits (0-9)
- Underscores (_)
- Hyphens (-)
- Colons (:)

**Examples**:
- Simple Moving Average: `SMA-20:SMA` (20-period SMA with label "SMA")
- Bollinger Band upper: `BB-20:BB:upper` (20-period Bollinger Band, upper field)
- RSI: `RSI-14:RSI` (14-period RSI)

## Operator Precedence

Operators are evaluated in the following order (highest to lowest precedence):

1. **Function calls**: `MIN()`, `MAX()`, `ABS()`, etc.
2. **Array indexing**: `CLOSE[1]`, `SMA[3]`
3. **Multiplication, Division, Modulo**: `*`, `/`, `%`
4. **Addition, Subtraction**: `+`, `-`
5. **Comparison**: `>`, `<`, `>=`, `<=`, `==`, `!=`
6. **Logical NOT**: `NOT`, `!`
7. **Logical AND**: `AND`
8. **Logical OR**: `OR`

Use parentheses to override precedence: `(A + B) * C`, `(CLOSE > OPEN) AND (VOLUME > 1000)`

## Technical Details

### Lookback History
- Maximum lookback: 200 bars (configurable)
- History is maintained as a sliding window
- Strategies skip evaluation until sufficient history is available
- Lookback offset 1 = previous bar, 2 = 2 bars ago, etc.

### Float Comparison
- Equality operators (`==`, `!=`) use tolerance of 1e-9 to handle floating-point precision
- For exact integer comparisons, this tolerance has no practical effect

### Time Zone Handling
- All time variables are converted to Eastern Time (America/New_York)
- Useful for market hours filtering (regular hours: 9:30 AM - 4:00 PM ET)
- Extended hours: 4:00 AM - 9:30 AM and 4:00 PM - 8:00 PM ET

### Cross Detection
- `CROSSES_ABOVE` and `CROSSES_BELOW` require at least 2 bars of history
- Returns 1.0 (true) on the exact bar where cross occurs, 0.0 otherwise
- Can be used with `WITHIN` for recent cross detection

### Temporal Functions
- `WITHIN(condition, N)` checks last N bars (excluding current)
- `BARS_SINCE(condition)` returns 0 if true now, 1 if true last bar, etc.
- If condition never met, `BARS_SINCE` returns 999999

### Error Handling
- Missing indicator data: strategy skips evaluation for that candle
- Insufficient history: strategy skips evaluation until enough bars available
- Division by zero: raises error and halts evaluation
- Invalid syntax: validation fails before execution

## Rule Examples

### Basic Long Entry/Exit

```text
long_entry:  CLOSE < OPEN AND !IN_BULL_TRADE
long_exit_1: CLOSE > OPEN AND IN_BULL_TRADE
```

### V2 Long + Short with Multiple Exit Rules

```text
long_entry:  SMA-20:SMA > SMA-50:SMA AND CLOSE < SMA-20:SMA AND !IN_BULL_TRADE
long_exit_1: CLOSE > SMA-20:SMA AND IN_BULL_TRADE         # (take profit)
long_exit_2: CLOSE < SMA-50:SMA AND IN_BULL_TRADE         # (stop loss)

short_entry: SMA-20:SMA < SMA-50:SMA AND CLOSE > SMA-20:SMA AND !IN_BEAR_TRADE
short_exit_1: CLOSE < SMA-20:SMA AND IN_BEAR_TRADE        # (take profit)
short_exit_2: CLOSE > SMA-50:SMA AND IN_BEAR_TRADE        # (stop loss)
```

**Note**: Exit rules are OR-ed per side. If any long exit rule matches, the long position exits. Same behavior for short exits.

### Using Parentheses and Arithmetic

```text
long_entry:  CLOSE < (HIGH + LOW) / 2 AND !IN_BULL_TRADE
long_exit_1: CLOSE > (HIGH + LOW) / 2 AND IN_BULL_TRADE
```

### Using Equality Operators

```text
long_entry:  CLOSE >= LOW[1] AND CLOSE <= HIGH[1] AND !IN_BULL_TRADE
long_exit_1: CLOSE > HIGH[1] AND IN_BULL_TRADE
```

### Using Lookback References (Compare to Previous Bars)

```text
long_entry:  CLOSE > CLOSE[1] AND CLOSE[1] > CLOSE[2] AND !IN_BULL_TRADE
long_exit_1: CLOSE < CLOSE[1] AND IN_BULL_TRADE
```

**Explanation**: Enter when price is rising for 2+ consecutive bars, exit when price drops

### Using Cross Functions (Golden Cross Strategy)

```text
long_entry:  CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA) AND !IN_BULL_TRADE
long_exit_1: CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA) AND IN_BULL_TRADE
```

**Explanation**: 
- Long Entry: When the 20-period SMA crosses above the 50-period SMA (golden cross)
- Long Exit: When the 20-period SMA crosses below the 50-period SMA (death cross)

### Using MIN/MAX Functions

```text
long_entry:  CLOSE < MIN(LOW[1], LOW[2], LOW[3]) AND !IN_BULL_TRADE
long_exit_1: CLOSE > MAX(HIGH[1], HIGH[2]) AND IN_BULL_TRADE
```

**Explanation**: Enter when price breaks below the lowest low of the last 3 bars

### Using Time Filtering (Market Hours Only)

```text
long_entry:  TIME >= 1030 AND TIME < 1400 AND CLOSE < OPEN AND !IN_BULL_TRADE
long_exit_1: CLOSE > OPEN AND IN_BULL_TRADE
```

**Explanation**: Only enter trades between 10:30 AM and 2:00 PM ET, exit anytime

### Using Built-in Candle Variables

```text
long_entry:  BODY > 2 AND LOWER_WICK < BODY * 0.2 AND !IN_BULL_TRADE
long_exit_1: BODY < 0.5 AND IN_BULL_TRADE
```

**Explanation**: Enter on strong bullish candles with small lower wicks, exit on small-bodied candles

### Using WITHIN for Recent Crossovers

```text
long_entry:  WITHIN(CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA), 50) AND CLOSE > OPEN AND !IN_BULL_TRADE
long_exit_1: CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA) AND IN_BULL_TRADE
```

**Explanation**: Enter if golden cross happened within last 50 bars AND we have a green candle today

### Using BARS_SINCE

```text
long_entry:  BARS_SINCE(CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA)) < 5 AND !IN_BULL_TRADE
long_exit_1: BARS_SINCE(CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA)) < 2 AND IN_BULL_TRADE
```

**Explanation**: Enter within 5 bars of golden cross, exit within 2 bars of death cross

### Using Indicator Variables (Bollinger Bands)

```text
long_entry:  CLOSE < BB-20:BB:lower AND !IN_BULL_TRADE
long_exit_1: CLOSE > BB-20:BB:upper AND IN_BULL_TRADE
```

**Explanation**: Enter when price touches lower Bollinger Band, exit at upper band

### Complex Strategy Example (Mean Reversion with Filters)

```text
long_entry:  CLOSE < BB-20:BB:lower AND RSI-14:RSI < 30 AND TIME >= 1000 AND TIME < 1500 AND VOLUME > VOLUME[1] AND !IN_BULL_TRADE
long_exit_1: (CLOSE > BB-20:BB:center OR BARS_SINCE(CLOSE < BB-20:BB:lower) > 10) AND IN_BULL_TRADE
```

**Explanation**: 
- Long Entry: Oversold conditions (below BB lower, RSI < 30), during market hours, with volume confirmation
- Long Exit: Price returns to BB center OR we've been in the trade for more than 10 bars

## Strategy Lifecycle API

Base URL examples assume backend on `http://localhost:8001`.

### List strategies

`GET /api/sessions/{session_id}/trade-strategies`

### Get one strategy

`GET /api/sessions/{session_id}/trade-strategies/{strategy_name}`

### Save strategy

`PUT /api/sessions/{session_id}/trade-strategies/{strategy_name}`

Body:

```json
{
  "description": "Optional notes",
  "long_entry_rule": "CLOSE < OPEN AND !IN_BULL_TRADE",
  "long_exit_rules": [
    "CLOSE > OPEN AND IN_BULL_TRADE",
    "CLOSE < LOW[2] AND IN_BULL_TRADE"
  ],
  "short_entry_rule": "CLOSE > OPEN AND !IN_BEAR_TRADE",
  "short_exit_rules": [
    "CLOSE < OPEN AND IN_BEAR_TRADE"
  ]
}
```

### Delete strategy

`DELETE /api/sessions/{session_id}/trade-strategies/{strategy_name}`

### Validate rules

`POST /api/sessions/{session_id}/trade-strategies/validate`

Body:

```json
{
  "long_entry_rule": "CLOSE < OPEN AND !IN_BULL_TRADE",
  "long_exit_rules": ["CLOSE > OPEN AND IN_BULL_TRADE"],
  "short_entry_rule": "",
  "short_exit_rules": []
}
```

### Apply strategy

`POST /api/sessions/{session_id}/trade-strategies/apply`

Body options:

- Apply a saved strategy by name:

```json
{
  "strategy_name": "MVP Strategy"
}
```

- Apply ad-hoc rules:

```json
{
  "strategy_name": "Unsaved Strategy",
  "long_entry_rule": "CLOSE < OPEN AND !IN_BULL_TRADE",
  "long_exit_rules": ["CLOSE > OPEN AND IN_BULL_TRADE"],
  "short_entry_rule": "",
  "short_exit_rules": []
}
```

Response includes:

- `markers`: LONG/SHORT entry/exit records with timestamp and action
- `marker_count`: number of generated markers

## Notes for Future Enhancements

Phase 1 provides production-ready features for most trading strategies. Future enhancements could include:

### Multi-Rule Exit Design
- Long and short exits support multiple formulas
- Exit behavior is OR-based per side (first matching condition exits)
- Common setup: one take-profit rule + one stop-loss rule + optional time-based exit rule

### Additional Functions
- Statistical: `AVG()`, `STDDEV()`, `SUM()`
- Candle patterns: `IS_DOJI()`, `IS_HAMMER()`, `IS_ENGULFING()`
- Bar state: `IS_FINAL` to filter by bar finalization status

### Position-Aware Variables
- `SHARES_HELD` - Current position size
- `ENTRY_PRICE` - Average entry price for current position
- `UNREALIZED_PNL` - Mark-to-market P&L

### Multiple Positions
- Track multiple concurrent positions
- Position pyramiding
- Partial exits

For LLM integration, use these rules as canonical target output:

- Generate valid `long_entry_rule` and `long_exit_rules` strings
- Generate valid optional `short_entry_rule` and `short_exit_rules` strings
- Keep variable names exactly as provided by the variable endpoint
- Prefer explicit parentheses when composing multi-clause expressions
- Use descriptive strategy names

## Trade Manager Widget Metrics (Real Data)

The 8 Trade Manager chips and sparkline are computed from live/snapshot candles plus strategy markers returned by `apply`.

### Capital & Position Rules

- Starting capital: `$10,000`
- Position sizing: buy the **maximum affordable lots of 100 shares** on each `LONG_ENTRY`
- Exit sizing: close the full open position on side-specific exit (`LONG_EXIT` or `SHORT_EXIT`)
- Fill price: candle `close` at the marker timestamp
- Strategy state: one active bull position at a time (`IN_BULL_TRADE`)

### Metric Definitions

- `Total P/L`: `final_equity - 10,000` (includes unrealized P/L if still in a position)
- `Total Trades`: count of closed trades (`LONG_ENTRY`→`LONG_EXIT` or `SHORT_ENTRY`→`SHORT_EXIT`)
- `Win Rate`: `winning_trades / total_trades`
- `Max DD`: largest peak-to-trough drop of equity curve in dollars
- `Sharpe Ratio`: computed from **per-trade returns** with risk-free rate assumed `0`
- `Average Win`: mean dollar P/L of winning trades
- `Average Loss`: mean dollar P/L of losing trades
- `Max Loss`: worst single closed-trade dollar loss

### Equity Sparkline

- Equity is marked-to-market each candle as: `cash + shares * close`
- Sparkline uses this real equity series; no mock values are used
