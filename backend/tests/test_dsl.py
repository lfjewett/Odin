"""
DSL Parser and Validation Tests

Tests for the Trade Manager Phase 1 DSL including:
- Basic operators and comparisons
- Lookback references
- Time filtering
- Cross detection
- Temporal logic
- Math operators and functions
"""

from types import SimpleNamespace

import pytest
from app.agent_data_store import SessionDataStore
from app.models import build_area_field_variable_name, build_area_metadata_variable_name
import app.trade_engine as trade_engine
from app.trade_engine import (
    RuleParser,
    evaluate_research_expression,
    evaluate_strategy,
    validate_rule_ast,
    parse_rule,
    validate_strategy_v2,
)


def validate_long_only(entry_rule: str, exit_rule: str):
    return validate_strategy_v2(
        long_entry_rule=entry_rule,
        long_exit_rules=[exit_rule],
        short_entry_rule="",
        short_exit_rules=[],
    )


class TestBasicParsing:
    """Test basic tokenization and parsing"""
    
    def test_simple_comparison(self):
        """Test simple greater-than comparison"""
        ast = parse_rule("CLOSE > OPEN")
        assert ast["type"] == "compare"
        assert ast["operator"] == ">"
        assert ast["left"]["type"] == "identifier"
        assert ast["left"]["name"] == "CLOSE"
        assert ast["right"]["type"] == "identifier"
        assert ast["right"]["name"] == "OPEN"
    
    def test_equality_operators(self):
        """Test all equality operators"""
        for op, symbol in [(">=", ">="), ("<=", "<="), ("==", "=="), ("!=", "!=")]:
            ast = parse_rule(f"A {symbol} B")
            assert ast["operator"] == symbol
    
    def test_boolean_operators(self):
        """Test AND, OR, NOT"""
        ast = parse_rule("A > B AND C < D")
        assert ast["type"] == "and"
        
        ast = parse_rule("A > B OR C < D")
        assert ast["type"] == "or"
        
        ast = parse_rule("NOT A > B")
        assert ast["type"] == "not"


class TestIndicatorVariables:
    """Test parsing of indicator variable names with hyphens and colons"""
    
    def test_simple_indicator(self):
        """Test parsing SMA-20:SMA"""
        ast = parse_rule("SMA-20:SMA > CLOSE")
        assert ast["left"]["name"] == "SMA-20:SMA"
    
    def test_multi_output_indicator(self):
        """Test parsing Bollinger Band with field selector"""
        ast = parse_rule("CLOSE > BB-20:BB:upper")
        assert ast["right"]["name"] == "BB-20:BB:upper"
    
    def test_sma_crossover_not_bool_identifier(self):
        """Test that SMA-50:SMA is treated as value, not boolean identifier"""
        # This is the critical test: indicator variables should be values, not boolean identifiers
        ast = parse_rule("SMA-20:SMA > SMA-50:SMA AND CLOSE < SMA-20:SMA AND !IN_BULL_TRADE")
        
        # The AND at the top level should have a compare on the left
        assert ast["type"] == "and"
        assert ast["left"]["type"] == "and"
        assert ast["left"]["left"]["type"] == "compare"
        assert ast["left"]["left"]["left"]["name"] == "SMA-20:SMA"
        assert ast["left"]["left"]["right"]["name"] == "SMA-50:SMA"


class TestAreaMetadataVariableNames:
    """Test canonical and legacy metadata variable naming behavior for area outputs."""

    def test_canonical_metadata_name_drops_duplicate_output_label(self):
        output = {
            "schema": "area",
            "output_id": "Gungnir",
            "label": "Gungnir",
        }

        name = build_area_metadata_variable_name("Gungnir", output, "dist")
        assert name == "Gungnir:dist"

    def test_canonical_area_bounds_drop_duplicate_output_label(self):
        output = {
            "schema": "area",
            "output_id": "Gungnir",
            "label": "Gungnir",
        }

        upper_name = build_area_field_variable_name("Gungnir", output, "upper")
        lower_name = build_area_field_variable_name("Gungnir", output, "lower")
        assert upper_name == "Gungnir:upper"
        assert lower_name == "Gungnir:lower"

    def test_indicator_map_supports_canonical_and_legacy_metadata_names(self, monkeypatch):
        store = SessionDataStore(
            session_id="session-1",
            agent_id="ohlc-agent",
            symbol="TEST",
            interval="5m",
        )

        indicator_agent_id = "indicator-gungnir"
        ts = "2026-01-05T14:30:00+00:00"
        store.latest_non_ohlc_by_key[(indicator_agent_id, "rec-1")] = {
            "id": "rec-1",
            "ts": ts,
            "output_id": "Gungnir",
            "upper": 105.0,
            "lower": 95.0,
            "metadata": {"dist": 12.5},
        }

        fake_agent = SimpleNamespace(
            config=SimpleNamespace(
                agent_name="Gungnir",
                outputs=[
                    {
                        "schema": "area",
                        "output_id": "Gungnir",
                        "label": "Gungnir",
                    }
                ],
            )
        )

        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: fake_agent if agent_id == indicator_agent_id else None,
        )

        variables = trade_engine._build_indicator_variable_maps(store)

        assert variables["Gungnir:upper"][ts] == 105.0
        assert variables["Gungnir:Gungnir:upper"][ts] == 105.0
        assert variables["Gungnir:lower"][ts] == 95.0
        assert variables["Gungnir:Gungnir:lower"][ts] == 95.0
        assert variables["Gungnir:dist"][ts] == 12.5
        assert variables["Gungnir:Gungnir:meta_dist"][ts] == 12.5

    def test_single_area_output_without_output_id_still_evaluates(self, monkeypatch):
        store = SessionDataStore(
            session_id="session-1",
            agent_id="ohlc-agent",
            symbol="TEST",
            interval="5m",
        )

        candles = [
            {"id": "c1", "ts": "2026-01-05T14:35:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            {"id": "c2", "ts": "2026-01-05T20:55:00+00:00", "open": 100, "high": 101, "low": 98, "close": 99, "volume": 1000},
        ]
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle

        indicator_agent_id = "indicator-gungnir"
        store.latest_non_ohlc_by_key[(indicator_agent_id, "rec-1")] = {
            "id": "rec-1",
            "ts": "2026-01-05T14:35:00+00:00",
            "upper": 110.0,
            "lower": 100.0,
            "metadata": {"dist": 10.0},
        }
        store.latest_non_ohlc_by_key[(indicator_agent_id, "rec-2")] = {
            "id": "rec-2",
            "ts": "2026-01-05T20:55:00+00:00",
            "upper": 111.0,
            "lower": 101.0,
            "metadata": {"dist": 10.0},
        }

        fake_agent = SimpleNamespace(
            config=SimpleNamespace(
                agent_name="Gungnir",
                outputs=[
                    {
                        "schema": "area",
                        "output_id": "Gungnir",
                        "label": "Gungnir",
                    }
                ],
            )
        )

        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: fake_agent if agent_id == indicator_agent_id else None,
        )

        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="gungnir-area-entry",
            long_entry_rules=[
                "TIME > 0930 AND TIME < 1550 AND Gungnir:upper > Gungnir:lower AND LOW < Gungnir:lower AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["IN_BULL_TRADE AND TIME == 1555"],
            short_entry_rules=[],
            short_exit_rules=[],
            data_store=store,
        )


class TestResearchExpressionEvaluation:
    def test_boolean_expression_renders_binary_line(self, monkeypatch):
        store = SessionDataStore(
            session_id="session-r1",
            agent_id="ohlc-agent",
            symbol="SPY",
            interval="5m",
        )

        candles = [
            {"id": "c1", "ts": "2026-01-05T14:35:00+00:00", "open": 101, "high": 106, "low": 99, "close": 100, "volume": 1000},
            {"id": "c2", "ts": "2026-01-05T20:55:00+00:00", "open": 100, "high": 104, "low": 98, "close": 99, "volume": 1000},
        ]
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle

        indicator_agent_id = "indicator-gungnir"
        store.latest_non_ohlc_by_key[(indicator_agent_id, "r1")] = {
            "id": "r1",
            "ts": "2026-01-05T14:35:00+00:00",
            "output_id": "Gungnir",
            "upper": 90.0,
            "lower": 100.0,
        }
        store.latest_non_ohlc_by_key[(indicator_agent_id, "r2")] = {
            "id": "r2",
            "ts": "2026-01-05T20:55:00+00:00",
            "output_id": "Gungnir",
            "upper": 90.0,
            "lower": 100.0,
        }

        fake_agent = SimpleNamespace(
            config=SimpleNamespace(
                agent_name="Gungnir",
                outputs=[{"schema": "area", "output_id": "Gungnir", "label": "Gungnir"}],
            )
        )
        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: fake_agent if agent_id == indicator_agent_id else None,
        )

        result = evaluate_research_expression(
            session_id="session-r1",
            expression="TIME > 0930 AND TIME < 1550 AND Gungnir:upper < Gungnir:lower AND HIGH > Gungnir:lower",
            output_schema="line",
            data_store=store,
        )

        assert result["schema"] == "line"
        assert result["count"] == 2
        assert [record["value"] for record in result["records"]] == [1.0, 0.0]

    def test_numeric_expression_renders_area_split(self, monkeypatch):
        store = SessionDataStore(
            session_id="session-r2",
            agent_id="ohlc-agent",
            symbol="SPY",
            interval="5m",
        )

        candles = [
            {"id": "c1", "ts": "2026-01-06T14:35:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            {"id": "c2", "ts": "2026-01-06T14:40:00+00:00", "open": 100, "high": 101, "low": 98, "close": 99, "volume": 1000},
        ]
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle

        indicator_agent_id = "indicator-gungnir"
        store.latest_non_ohlc_by_key[(indicator_agent_id, "r1")] = {
            "id": "r1",
            "ts": "2026-01-06T14:35:00+00:00",
            "output_id": "Gungnir",
            "upper": 110.0,
            "lower": 100.0,
        }
        store.latest_non_ohlc_by_key[(indicator_agent_id, "r2")] = {
            "id": "r2",
            "ts": "2026-01-06T14:40:00+00:00",
            "output_id": "Gungnir",
            "upper": 95.0,
            "lower": 100.0,
        }

        fake_agent = SimpleNamespace(
            config=SimpleNamespace(
                agent_name="Gungnir",
                outputs=[{"schema": "area", "output_id": "Gungnir", "label": "Gungnir"}],
            )
        )
        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: fake_agent if agent_id == indicator_agent_id else None,
        )

        result = evaluate_research_expression(
            session_id="session-r2",
            expression="Gungnir:upper - Gungnir:lower",
            output_schema="area",
            data_store=store,
        )

        assert result["schema"] == "area"
        assert result["count"] == 2
        assert result["records"][0]["upper"] == 10.0
        assert result["records"][0]["lower"] == 0.0
        assert result["records"][1]["upper"] == 0.0
        assert result["records"][1]["lower"] == -5.0
        assert result["records"][0]["metadata"]["subgraph"] is True


class TestBooleanIdentifiers:
    """Test position state variables"""
    
    def test_in_bull_trade_as_boolean(self):
        """Test IN_BULL_TRADE used as boolean identifier"""
        ast = parse_rule("IN_BULL_TRADE")
        assert ast["type"] == "bool_identifier"
        assert ast["name"] == "IN_BULL_TRADE"
    
    def test_in_bear_trade_as_boolean(self):
        """Test IN_BEAR_TRADE used as boolean identifier"""
        ast = parse_rule("IN_BEAR_TRADE")
        assert ast["type"] == "bool_identifier"
        assert ast["name"] == "IN_BEAR_TRADE"
    
    def test_negated_boolean(self):
        """Test !IN_BULL_TRADE"""
        ast = parse_rule("!IN_BULL_TRADE")
        assert ast["type"] == "not"
        assert ast["operand"]["type"] == "bool_identifier"


class TestLookback:
    """Test lookback references with array indexing"""
    
    def test_lookback_one_bar(self):
        """Test CLOSE[1] for previous bar"""
        ast = parse_rule("CLOSE > CLOSE[1]")
        assert ast["right"]["type"] == "indexed_variable"
        assert ast["right"]["name"] == "CLOSE"
        assert ast["right"]["offset"] == 1
    
    def test_lookback_multiple_bars(self):
        """Test CLOSE[10] for 10 bars ago"""
        ast = parse_rule("CLOSE > CLOSE[10]")
        assert ast["right"]["offset"] == 10
    
    def test_lookback_with_indicator(self):
        """Test SMA-20:SMA[3]"""
        ast = parse_rule("SMA-20:SMA > SMA-20:SMA[1]")
        assert ast["left"]["name"] == "SMA-20:SMA"
        assert ast["left"]["type"] == "identifier"
        assert ast["right"]["name"] == "SMA-20:SMA"
        assert ast["right"]["offset"] == 1


class TestMathOperators:
    """Test arithmetic operators with precedence"""
    
    def test_addition(self):
        """Test addition operator"""
        ast = parse_rule("CLOSE > OPEN + 1")
        assert ast["right"]["type"] == "arithmetic"
        assert ast["right"]["operator"] == "+"
    
    def test_multiplication_precedence(self):
        """Test that * has higher precedence than +"""
        ast = parse_rule("HIGH + LOW / 2 > CLOSE")
        # Should be parsed as: HIGH + (LOW / 2), not (HIGH + LOW) / 2
        right_operand = ast["right"]
        assert right_operand["type"] == "identifier"
        assert right_operand["name"] == "CLOSE"
        
        left_operand = ast["left"]
        assert left_operand["type"] == "arithmetic"
        assert left_operand["operator"] == "+"
        assert left_operand["right"]["type"] == "arithmetic"
        assert left_operand["right"]["operator"] == "/"
    
    def test_parentheses_override(self):
        """Test that parentheses override precedence"""
        ast = parse_rule("(HIGH + LOW) / 2 > CLOSE")
        # The division should be at top level left, its left should be addition in parens
        assert ast["left"]["type"] == "arithmetic"
        assert ast["left"]["operator"] == "/"
        assert ast["left"]["left"]["type"] == "arithmetic"
        assert ast["left"]["left"]["operator"] == "+"
    
    def test_modulo(self):
        """Test modulo operator"""
        ast = parse_rule("CLOSE % 5 == 0")
        assert ast["left"]["type"] == "arithmetic"
        assert ast["left"]["operator"] == "%"


class TestFunctions:
    """Test function call parsing"""
    
    def test_min_function(self):
        """Test MIN function"""
        ast = parse_rule("CLOSE > MIN(LOW[1], LOW[2], LOW[3])")
        assert ast["right"]["type"] == "function"
        assert ast["right"]["name"] == "MIN"
        assert len(ast["right"]["args"]) == 3
    
    def test_max_function(self):
        """Test MAX function"""
        ast = parse_rule("CLOSE < MAX(HIGH[1], HIGH[2])")
        assert ast["right"]["type"] == "function"
        assert ast["right"]["name"] == "MAX"
        assert len(ast["right"]["args"]) == 2
    
    def test_abs_function(self):
        """Test ABS function"""
        ast = parse_rule("ABS(CLOSE - OPEN) > 2")
        assert ast["left"]["type"] == "function"
        assert ast["left"]["name"] == "ABS"
        assert len(ast["left"]["args"]) == 1
    
    def test_function_case_insensitive(self):
        """Test that function names are case-insensitive"""
        ast = parse_rule("min(1, 2) > 0")
        assert ast["left"]["name"] == "min"  # Internally stored as parsed


class TestCrossFunctions:
    """Test cross detection functions"""
    
    def test_crosses_above(self):
        """Test CROSSES_ABOVE function"""
        ast = parse_rule("CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA) > 0")
        assert ast["left"]["type"] == "function"
        assert ast["left"]["name"] == "CROSSES_ABOVE"
        assert len(ast["left"]["args"]) == 2
    
    def test_crosses_below(self):
        """Test CROSSES_BELOW function"""
        ast = parse_rule("CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA) > 0")
        assert ast["left"]["type"] == "function"
        assert ast["left"]["name"] == "CROSSES_BELOW"


class TestTimeVariables:
    """Test time-based variables"""
    
    def test_time_variable(self):
        """Test TIME variable (HHMM format)"""
        ast = parse_rule("TIME > 1030")
        assert ast["left"]["name"] == "TIME"
    
    def test_hour_variable(self):
        """Test HOUR variable"""
        ast = parse_rule("HOUR >= 10")
        assert ast["left"]["name"] == "HOUR"
    
    def test_minute_variable(self):
        """Test MINUTE variable"""
        ast = parse_rule("MINUTE < 30")
        assert ast["left"]["name"] == "MINUTE"
    
    def test_market_hours_filter(self):
        """Test typical market hours filter"""
        ast = parse_rule("TIME >= 1030 AND TIME < 1400")
        assert ast["type"] == "and"


class TestBuiltinCandles:
    """Test built-in candle variables"""
    
    def test_body_variable(self):
        """Test BODY = ABS(CLOSE - OPEN)"""
        ast = parse_rule("BODY > 2")
        assert ast["left"]["name"] == "BODY"
    
    def test_range_variable(self):
        """Test RANGE = HIGH - LOW"""
        ast = parse_rule("RANGE < 5")
        assert ast["left"]["name"] == "RANGE"
    
    def test_upper_wick_variable(self):
        """Test UPPER_WICK variable"""
        ast = parse_rule("UPPER_WICK > 1")
        assert ast["left"]["name"] == "UPPER_WICK"
    
    def test_lower_wick_variable(self):
        """Test LOWER_WICK variable"""
        ast = parse_rule("LOWER_WICK < 0.5")
        assert ast["left"]["name"] == "LOWER_WICK"


class TestPositionAwareVariables:
    """Test runtime support for position-aware variables like ENTRY_PRICE"""

    def _build_store(self) -> SessionDataStore:
        store = SessionDataStore(session_id="session-1", agent_id="ohlc-agent", symbol="TEST", interval="5m")
        candles = [
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00", "open": 103, "high": 104, "low": 102, "close": 103, "volume": 1000},
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
            {"id": "c4", "ts": "2026-01-05T14:45:00+00:00", "open": 98, "high": 99, "low": 97, "close": 98, "volume": 1000},
        ]
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle
        return store

    def test_entry_price_drives_long_and_short_exits(self):
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="entry-price-test",
            long_entry_rule="TIME == 930 AND !IN_BULL_TRADE",
            long_exit_rules=["IN_BULL_TRADE AND CLOSE > ENTRY_PRICE + 2"],
            short_entry_rule="TIME == 940 AND !IN_BEAR_TRADE AND !IN_BULL_TRADE",
            short_exit_rules=["IN_BEAR_TRADE AND CLOSE < ENTRY_PRICE - 2"],
            data_store=self._build_store(),
        )

        assert [marker["action"] for marker in result["markers"]] == [
            "LONG_ENTRY",
            "LONG_EXIT",
            "SHORT_ENTRY",
            "SHORT_EXIT",
        ]
        assert result["performance"]["total_trades"] == 2
        assert result["performance"]["final_equity"] > result["performance"]["starting_capital"]

    def test_long_and_short_can_be_open_together(self):
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="dual-side-test",
            long_entry_rule="TIME == 930 AND !IN_BULL_TRADE",
            long_exit_rules=["TIME == 935 AND IN_BULL_TRADE AND ENTRY_PRICE == 100"],
            short_entry_rule="TIME == 930 AND !IN_BEAR_TRADE",
            short_exit_rules=["TIME == 940 AND IN_BEAR_TRADE AND ENTRY_PRICE == 100"],
            data_store=self._build_store(),
        )

        assert [marker["action"] for marker in result["markers"]] == [
            "LONG_ENTRY",
            "SHORT_ENTRY",
            "LONG_EXIT",
            "SHORT_EXIT",
        ]
        assert result["performance"]["total_trades"] == 2


class TestWithinFunction:
    """Test WITHIN temporal logic"""
    
    def test_within_function(self):
        """Test WITHIN(condition, bars) function"""
        # Note: WITHIN is special and handled at expression level
        ast = parse_rule("WITHIN(CLOSE > OPEN, 50) AND !IN_BULL_TRADE")
        # The parser should handle this at the expression level
        assert ast["type"] == "and"


class TestValidation:
    """Test strategy validation"""
    
    def test_valid_entry_exit(self):
        """Test basic valid entry/exit rules"""
        result = validate_long_only(
            "CLOSE > OPEN AND !IN_BULL_TRADE",
            "CLOSE < OPEN AND IN_BULL_TRADE"
        )
        assert result.valid
        assert len(result.errors) == 0
    
    def test_valid_with_indicators(self):
        """Test valid rules with indicators"""
        result = validate_long_only(
            "SMA-20:SMA > SMA-50:SMA AND CLOSE < SMA-20:SMA AND !IN_BULL_TRADE",
            "SMA-20:SMA < SMA-50:SMA AND IN_BULL_TRADE"
        )
        assert result.valid
        assert len(result.errors) == 0
    
    def test_valid_with_lookback(self):
        """Test valid rules with lookback"""
        result = validate_long_only(
            "CLOSE > CLOSE[1] AND CLOSE[1] > CLOSE[2] AND !IN_BULL_TRADE",
            "CLOSE < CLOSE[1] AND IN_BULL_TRADE"
        )
        assert result.valid
        assert len(result.errors) == 0
    
    def test_valid_with_math(self):
        """Test valid rules with math operators"""
        result = validate_long_only(
            "(CLOSE > (HIGH + LOW) / 2) AND !IN_BULL_TRADE",
            "CLOSE < OPEN AND IN_BULL_TRADE"
        )
        assert result.valid
        assert len(result.errors) == 0
    
    def test_valid_with_functions(self):
        """Test valid rules with functions"""
        result = validate_long_only(
            "CLOSE < MIN(LOW[1], LOW[2]) AND !IN_BULL_TRADE",
            "CLOSE > MAX(HIGH[1], HIGH[2]) AND IN_BULL_TRADE"
        )
        assert result.valid
        assert len(result.errors) == 0
    
    def test_invalid_unknown_bool_identifier(self):
        """Test that unknown boolean identifiers fail validation"""
        result = validate_long_only(
            "UNKNOWN_VAR AND !IN_BULL_TRADE",
            "CLOSE > OPEN"
        )
        assert not result.valid
        assert any("not supported" in err.lower() for err in result.errors)
    
    def test_empty_rules(self):
        """Test that empty rules fail validation"""
        result = validate_long_only("", "")
        assert not result.valid
        assert any("empty" in err.lower() for err in result.errors)
    
    def test_syntax_error(self):
        """Test that syntax errors are caught"""
        result = validate_long_only(
            "CLOSE > OPEN AND",  # Incomplete AND
            "CLOSE < OPEN"
        )
        assert not result.valid
        assert len(result.errors) > 0


class TestComplexStrategies:
    """Test complex real-world strategy patterns"""
    
    def test_golden_cross_strategy(self):
        """Test golden cross strategy with cross detection"""
        result = validate_long_only(
            "CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA) AND !IN_BULL_TRADE",
            "CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA) AND IN_BULL_TRADE"
        )
        assert result.valid
    
    def test_bollinger_band_strategy(self):
        """Test Bollinger Band mean reversion"""
        result = validate_long_only(
            "CLOSE < BB-20:BB:lower AND RSI-14:RSI < 30 AND !IN_BULL_TRADE",
            "CLOSE > BB-20:BB:center AND IN_BULL_TRADE"
        )
        assert result.valid
    
    def test_market_hours_only_strategy(self):
        """Test strategy with market hours filter"""
        result = validate_long_only(
            "TIME >= 1030 AND TIME < 1400 AND CLOSE < OPEN AND !IN_BULL_TRADE",
            "CLOSE > OPEN AND IN_BULL_TRADE"
        )
        assert result.valid
    
    def test_breakout_with_lookback(self):
        """Test breakout strategy using lookback"""
        result = validate_long_only(
            "CLOSE > MAX(HIGH[1], HIGH[2], HIGH[3]) AND !IN_BULL_TRADE",
            "CLOSE < MIN(LOW[1], LOW[2]) AND IN_BULL_TRADE"
        )
        assert result.valid
    
    def test_candle_pattern_strategy(self):
        """Test strategy using candle variables"""
        result = validate_long_only(
            "BODY > 2 AND LOWER_WICK < BODY * 0.2 AND !IN_BULL_TRADE",
            "BODY < 0.5 AND IN_BULL_TRADE"
        )
        assert result.valid


class TestDailyPnl:
    """Test DAILY_PNL variable — resets each ET day, gates on daily realized P&L"""

    def _build_store(self, candles: list[dict]) -> SessionDataStore:
        store = SessionDataStore(session_id="session-1", agent_id="ohlc-agent", symbol="TEST", interval="5m")
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle
        return store

    def test_daily_pnl_gates_reentry_after_win(self):
        """After a winning trade, DAILY_PNL > 0 should block further entries that day.
        On the next day DAILY_PNL resets to 0 and entries are allowed again."""
        candles = [
            # Day 1 (2026-01-05, all times ET)
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},  # 9:30 ET - bearish, entry
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00", "open": 100, "high": 104, "low": 99, "close": 103, "volume": 1000},  # 9:35 ET - bullish, exit (win)
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},  # 9:40 ET - bearish signal but DAILY_PNL > 0 → blocked
            # Day 2 (2026-01-06)
            {"id": "c4", "ts": "2026-01-06T14:30:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},  # 9:30 ET - bearish, DAILY_PNL reset → entry
            {"id": "c5", "ts": "2026-01-06T14:35:00+00:00", "open": 100, "high": 104, "low": 99, "close": 103, "volume": 1000},  # 9:35 ET - bullish, exit (win)
        ]
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="daily-pnl-gate",
            long_entry_rule="CLOSE < OPEN AND !IN_BULL_TRADE AND DAILY_PNL <= 0",
            long_exit_rules=["CLOSE > OPEN AND IN_BULL_TRADE"],
            data_store=self._build_store(candles),
        )
        actions = [m["action"] for m in result["markers"]]
        # c1 entry, c2 exit, c3 blocked (DAILY_PNL > 0), c4 entry (new day), c5 exit
        assert actions == ["LONG_ENTRY", "LONG_EXIT", "LONG_ENTRY", "LONG_EXIT"]
        assert result["performance"]["total_trades"] == 2

    def test_daily_pnl_continues_after_loss(self):
        """After a losing trade, DAILY_PNL < 0, so entries continue until P&L recovers."""
        candles = [
            # c1: bearish candle → entry at 100
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            # c2: bullish candle (open=94, close=97) → exit rule fires (CLOSE>OPEN), but
            #     close < entry_price → LOSS; DAILY_PNL < 0 after this
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00", "open": 94,  "high": 98,  "low": 93, "close": 97,  "volume": 1000},
            # c3: bearish → DAILY_PNL <= 0 so re-entry is allowed
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            # c4: bullish above entry → exit fires (WIN)
            {"id": "c4", "ts": "2026-01-05T14:45:00+00:00", "open": 100, "high": 105, "low": 99, "close": 104, "volume": 1000},
        ]
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="daily-pnl-continue",
            long_entry_rule="CLOSE < OPEN AND !IN_BULL_TRADE AND DAILY_PNL <= 0",
            long_exit_rules=["CLOSE > OPEN AND IN_BULL_TRADE"],
            data_store=self._build_store(candles),
        )
        actions = [m["action"] for m in result["markers"]]
        assert actions == ["LONG_ENTRY", "LONG_EXIT", "LONG_ENTRY", "LONG_EXIT"]
        assert result["performance"]["total_trades"] == 2

    def test_daily_pnl_parses_and_validates(self):
        """DAILY_PNL is a valid identifier in rules."""
        result = validate_long_only(
            "CLOSE < OPEN AND !IN_BULL_TRADE AND DAILY_PNL <= 0",
            "CLOSE > OPEN AND IN_BULL_TRADE",
        )
        assert result.valid


class TestLongEntriesSince:
    """Test LONG_ENTRIES_SINCE(cond) and SHORT_ENTRIES_SINCE(cond) — per-trend entry counter"""

    def _build_store(self, candles: list[dict]) -> SessionDataStore:
        store = SessionDataStore(session_id="session-1", agent_id="ohlc-agent", symbol="TEST", interval="5m")
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle
        return store

    def test_long_entries_since_limits_to_first_entry(self):
        """Only the first entry is taken after a condition rising edge; subsequent signals blocked."""
        candles = [
            # close<=100 (condition false), close>100 triggers rising edge, then stays true
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00", "open": 99,  "high": 100, "low": 98,  "close": 99,  "volume": 1000},  # CLOSE>100=false
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1000},  # CLOSE>100=true (rising edge → entry)
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00", "open": 102, "high": 104, "low": 101, "close": 103, "volume": 1000},  # CLOSE>100=true (no new edge → blocked)
            {"id": "c4", "ts": "2026-01-05T14:45:00+00:00", "open": 103, "high": 104, "low": 98,  "close": 99,  "volume": 1000},  # exit (CLOSE<100)
            {"id": "c5", "ts": "2026-01-05T14:50:00+00:00", "open": 98,  "high": 100, "low": 97,  "close": 98,  "volume": 1000},  # CLOSE>100=false
            {"id": "c6", "ts": "2026-01-05T14:55:00+00:00", "open": 99,  "high": 104, "low": 99,  "close": 102, "volume": 1000},  # CLOSE>100=true (new rising edge → entry)
            {"id": "c7", "ts": "2026-01-05T15:00:00+00:00", "open": 102, "high": 104, "low": 98,  "close": 99,  "volume": 1000},  # exit
        ]
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="entries-since-first",
            long_entry_rule="!IN_BULL_TRADE AND LONG_ENTRIES_SINCE(CLOSE > 100) == 0 AND CLOSE > 100",
            long_exit_rules=["IN_BULL_TRADE AND CLOSE < 100"],
            data_store=self._build_store(candles),
        )
        actions = [m["action"] for m in result["markers"]]
        # c2 entry, c3 blocked, c4 exit, c5 no signal, c6 new trigger → entry, c7 exit
        assert actions == ["LONG_ENTRY", "LONG_EXIT", "LONG_ENTRY", "LONG_EXIT"]
        assert result["performance"]["total_trades"] == 2

    def test_long_entries_since_take_first_two(self):
        """With < 2 threshold, two entries are taken per trend episode then blocked.

        The trigger condition is TIME == 930 — it fires exactly once per day,
        creating a single rising edge. Subsequent exits/re-entries stay within
        the same episode so the count accumulates to 2 and then blocks further
        entries for the rest of the session.
        """
        candles = [
            # c0: 9:25 warmup — TIME=925≠930 so trigger condition is false
            {"id": "c0", "ts": "2026-01-05T14:25:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            # c1: 9:30 — TIME==930 → rising edge; CLOSE<OPEN → entry #1 (ENTRIES_SINCE=0<2)
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            # c2: 9:35 — CLOSE>OPEN → exit fires; CLOSE<OPEN=false so no re-entry here
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00", "open": 100, "high": 104, "low": 99, "close": 104, "volume": 1000},
            # c3: 9:40 — CLOSE<OPEN; ENTRIES_SINCE trigger still at c1, count=1 < 2 → entry #2
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
            # c4: 9:45 — CLOSE>OPEN → exit fires; count now 2
            {"id": "c4", "ts": "2026-01-05T14:45:00+00:00", "open": 100, "high": 104, "low": 99, "close": 104, "volume": 1000},
            # c5: 9:50 — CLOSE<OPEN; ENTRIES_SINCE count=2, 2 < 2 = false → BLOCKED
            {"id": "c5", "ts": "2026-01-05T14:50:00+00:00", "open": 101, "high": 102, "low": 99, "close": 100, "volume": 1000},
        ]
        result = evaluate_strategy(
            session_id="session-1",
            strategy_name="entries-since-two",
            # Trend trigger: TIME == 930 fires once (rising edge). Entry signal: CLOSE < OPEN.
            long_entry_rule="!IN_BULL_TRADE AND LONG_ENTRIES_SINCE(TIME == 930) < 2 AND CLOSE < OPEN",
            long_exit_rules=["IN_BULL_TRADE AND CLOSE > OPEN"],
            data_store=self._build_store(candles),
        )
        actions = [m["action"] for m in result["markers"]]
        # c1 entry #1, c2 exit, c3 entry #2, c4 exit, c5 blocked (count=2)
        assert actions == ["LONG_ENTRY", "LONG_EXIT", "LONG_ENTRY", "LONG_EXIT"]
        assert result["performance"]["total_trades"] == 2

    def test_entries_since_parses_and_validates(self):
        """LONG_ENTRIES_SINCE and SHORT_ENTRIES_SINCE are valid in rules."""
        long_result = validate_long_only(
            "!IN_BULL_TRADE AND LONG_ENTRIES_SINCE(CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA)) == 0 AND CLOSE > OPEN",
            "IN_BULL_TRADE AND CLOSE < OPEN",
        )
        assert long_result.valid

        short_result = validate_strategy_v2(
            long_entry_rule="",
            long_exit_rules=[],
            short_entry_rule="!IN_BEAR_TRADE AND SHORT_ENTRIES_SINCE(CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA)) == 0 AND CLOSE < OPEN",
            short_exit_rules=["IN_BEAR_TRADE AND CLOSE > OPEN"],
        )
        assert short_result.valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
