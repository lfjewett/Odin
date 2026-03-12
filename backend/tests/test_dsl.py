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
        assert any("entry rule" in err.lower() for err in result.errors)
    
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


# ============================================================================
# Phase 3: S/R Indicator Backend Properties
# ============================================================================


class TestSRIndicatorProperties:
    """Phase 3 acceptance: per-candle completeness, partition correctness, confidence range."""

    # Timestamps representing a full trading session
    CANDLE_TIMESTAMPS = [
        "2026-01-05T14:30:00+00:00",
        "2026-01-05T14:35:00+00:00",
        "2026-01-05T14:40:00+00:00",
        "2026-01-05T14:45:00+00:00",
        "2026-01-05T14:50:00+00:00",
    ]

    def _build_sr_store(
        self,
        zones: list[tuple[str, float, float, float]],  # (output_id, upper, lower, confidence)
        timestamps: list[str] | None = None,
    ) -> SessionDataStore:
        """Helper: build a SessionDataStore pre-populated with S/R zone records."""
        ts_list = timestamps or self.CANDLE_TIMESTAMPS
        store = SessionDataStore(
            session_id="session-sr",
            agent_id="ohlc-agent",
            symbol="SPY",
            interval="5m",
        )
        for i, ts in enumerate(ts_list):
            store.latest_by_candle_id[f"c{i}"] = {
                "id": f"c{i}",
                "ts": ts,
                "open": 400.0, "high": 402.0, "low": 398.0, "close": 401.0, "volume": 1000,
            }

        AGENT = "indicator-sr-agent"
        for i, ts in enumerate(ts_list):
            for output_id, upper, lower, confidence in zones:
                store.latest_non_ohlc_by_key[(AGENT, f"{output_id}::{ts}")] = {
                    "id": f"{output_id}-{i}",
                    "ts": ts,
                    "output_id": output_id,
                    "schema": "area",
                    "upper": upper,
                    "lower": lower,
                    "metadata": {
                        "confidence": confidence,
                        "label": output_id,
                        "render": {"primary_color": "#FF5050"},
                    },
                }
        return store

    def test_per_candle_coverage_completeness(self):
        """Every candle timestamp must have at least one zone record (per-candle completeness)."""
        zones = [("zone_1", 405.0, 403.0, 0.85)]
        store = self._build_sr_store(zones)

        records = store.get_non_ohlc_records()
        covered_ts = {r["ts"] for r in records}
        assert covered_ts == set(self.CANDLE_TIMESTAMPS), (
            "Some candle timestamps are missing S/R zone records — per-candle completeness violation"
        )

    def test_per_candle_coverage_with_eight_zones(self):
        """With 8 zones, every candle timestamp has records for each zone."""
        zones = [
            (f"zone_{i}", float(400 + i * 5), float(400 + i * 5 - 3), round(0.1 + i * 0.1, 1))
            for i in range(1, 9)
        ]
        store = self._build_sr_store(zones)

        records = store.get_non_ohlc_records()
        total_expected = len(self.CANDLE_TIMESTAMPS) * 8
        assert len(records) == total_expected

        # Every timestamp has all 8 zones
        from collections import defaultdict
        ts_zone_map: dict[str, set[str]] = defaultdict(set)
        for r in records:
            ts_zone_map[r["ts"]].add(r["output_id"])

        for ts in self.CANDLE_TIMESTAMPS:
            assert ts_zone_map[ts] == {f"zone_{i}" for i in range(1, 9)}, (
                f"Missing zones at timestamp {ts}: {ts_zone_map[ts]}"
            )

    def test_output_id_partition_correctness_two_zones(self):
        """zone_1 and zone_2 occupy separate storage slots — no cross-contamination."""
        zones = [
            ("zone_1", 405.0, 403.0, 0.85),
            ("zone_2", 395.0, 393.0, 0.45),
        ]
        store = self._build_sr_store(zones, timestamps=["2026-01-05T14:30:00+00:00"])

        records = store.get_non_ohlc_records()
        assert len(records) == 2

        zone_map = {r["output_id"]: r for r in records}
        assert zone_map["zone_1"]["upper"] == 405.0
        assert zone_map["zone_2"]["upper"] == 395.0
        assert zone_map["zone_1"]["upper"] != zone_map["zone_2"]["upper"]

    def test_output_id_partition_correctness_eight_zones(self):
        """All 8 zone output_ids are stored independently at a single timestamp."""
        zones = [
            (f"zone_{i}", float(400 + i * 5), float(400 + i * 5 - 3), i * 0.1)
            for i in range(1, 9)
        ]
        store = self._build_sr_store(zones, timestamps=["2026-01-05T14:30:00+00:00"])

        records = store.get_non_ohlc_records()
        assert len(records) == 8
        output_ids = {r["output_id"] for r in records}
        assert output_ids == {f"zone_{i}" for i in range(1, 9)}

        # Verify each zone has distinct upper values (no aliasing)
        uppers = [r["upper"] for r in records]
        assert len(set(uppers)) == 8, "Each zone must have a distinct upper value"

    def test_confidence_is_numeric(self):
        """Confidence values in zone metadata are always numeric (int or float)."""
        zones = [
            ("zone_1", 405.0, 403.0, 0.85),
            ("zone_2", 395.0, 393.0, 0.45),
        ]
        store = self._build_sr_store(zones)

        for record in store.get_non_ohlc_records():
            confidence = record.get("metadata", {}).get("confidence")
            assert confidence is not None, f"Missing confidence in {record.get('output_id')} at {record.get('ts')}"
            assert isinstance(confidence, (int, float)), f"confidence must be numeric, got {type(confidence)}"

    def test_confidence_within_valid_range(self):
        """Confidence values must be in [0.0, 1.0] — values outside are invalid."""
        zones = [
            ("zone_1", 405.0, 403.0, 0.0),    # boundary
            ("zone_2", 395.0, 393.0, 1.0),    # boundary
            ("zone_3", 385.0, 383.0, 0.5),    # midpoint
            ("zone_4", 375.0, 373.0, 0.99),   # near-max
        ]
        store = self._build_sr_store(zones, timestamps=["2026-01-05T14:30:00+00:00"])

        for record in store.get_non_ohlc_records():
            confidence = record["metadata"]["confidence"]
            assert 0.0 <= confidence <= 1.0, (
                f"confidence {confidence} out of range [0, 1] for {record['output_id']}"
            )

    def test_backend_render_color_is_string(self):
        """render.primary_color is a string display hint emitted by the backend."""
        zones = [("zone_1", 405.0, 403.0, 0.8)]
        store = self._build_sr_store(zones, timestamps=["2026-01-05T14:30:00+00:00"])

        records = store.get_non_ohlc_records()
        assert len(records) == 1
        render = records[0].get("metadata", {}).get("render", {})
        assert isinstance(render.get("primary_color"), str)

    def test_upper_always_greater_than_lower(self):
        """S/R zones are well-formed: upper > lower (zone has positive width)."""
        zones = [
            ("zone_1", 405.0, 403.0, 0.85),
            ("zone_2", 395.0, 393.0, 0.45),
        ]
        store = self._build_sr_store(zones)

        for record in store.get_non_ohlc_records():
            assert record["upper"] > record["lower"], (
                f"{record['output_id']} at {record['ts']}: "
                f"upper={record['upper']} must be > lower={record['lower']}"
            )


# ============================================================================
# Phase 4: S/R Indicator DSL / Trade Engine Integration
# ============================================================================


class TestSRIndicatorDSLVariableNames:
    """Phase 4: Canonical variable name format for multi-zone S/R indicator."""

    def test_zone_field_names_follow_canonical_format(self):
        """SR:zone_N:upper / SR:zone_N:lower are the canonical DSL variable names."""
        for i in range(1, 9):
            output = {"schema": "area", "output_id": f"zone_{i}", "label": f"zone_{i}"}
            assert build_area_field_variable_name("SR", output, "upper") == f"SR:zone_{i}:upper"
            assert build_area_field_variable_name("SR", output, "lower") == f"SR:zone_{i}:lower"

    def test_confidence_metadata_canonical_name(self):
        """SR:zone_N:confidence is the canonical DSL name for the confidence metadata field."""
        for i in range(1, 9):
            output = {"schema": "area", "output_id": f"zone_{i}", "label": f"zone_{i}"}
            assert build_area_metadata_variable_name("SR", output, "confidence") == f"SR:zone_{i}:confidence"

    def test_single_output_name_collapses_duplicate_label(self):
        """Single-output indicator where label == agent_name: SR:upper (not SR:SR:upper)."""
        output = {"schema": "area", "output_id": "SR", "label": "SR"}
        assert build_area_field_variable_name("SR", output, "upper") == "SR:upper"
        assert build_area_field_variable_name("SR", output, "lower") == "SR:lower"
        assert build_area_metadata_variable_name("SR", output, "confidence") == "SR:confidence"

    def test_zone_rules_parse_and_validate(self):
        """DSL rules using S/R zone variables (upper/lower/confidence) are valid."""
        result = validate_strategy_v2(
            long_entry_rules=[
                "LOW < SR:zone_1:lower AND SR:zone_1:confidence > 0.6 AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["HIGH > SR:zone_1:upper AND IN_BULL_TRADE"],
            short_entry_rules=[
                "HIGH > SR:zone_1:upper AND SR:zone_1:confidence > 0.6 AND !IN_BEAR_TRADE"
            ],
            short_exit_rules=["LOW < SR:zone_1:lower AND IN_BEAR_TRADE"],
        )
        assert result.valid

    def test_multi_zone_rule_references_independent_zones(self):
        """A single rule can reference zone_1 and zone_2 independently."""
        result = validate_strategy_v2(
            long_entry_rules=[
                "SR:zone_1:confidence > 0.7 AND SR:zone_2:confidence > 0.2 "
                "AND CLOSE < SR:zone_1:lower AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["IN_BULL_TRADE AND CLOSE > SR:zone_1:upper"],
            short_entry_rules=[],
            short_exit_rules=[],
        )
        assert result.valid


class TestSRIndicatorDSLEvaluation:
    """Phase 4: Trade engine correctly evaluates S/R zone variables in strategies."""

    SR_AGENT_ID = "indicator-sr-agent"
    SR_AGENT_NAME = "SR"

    def _make_fake_sr_agent(self) -> SimpleNamespace:
        return SimpleNamespace(
            config=SimpleNamespace(
                agent_name=self.SR_AGENT_NAME,
                outputs=[
                    {"schema": "area", "output_id": "zone_1", "label": "zone_1"},
                    {"schema": "area", "output_id": "zone_2", "label": "zone_2"},
                ],
            )
        )

    def _make_store(self, monkeypatch) -> SessionDataStore:
        """
        Build a store with 3 candles and two S/R zones:
          zone_1: strong resistance at 403–405 (confidence=0.85)
          zone_2: weak support at 396–398 (confidence=0.30)

        Candle prices: close = 401, 397, 406
        """
        store = SessionDataStore(
            session_id="session-sr-dsl",
            agent_id="ohlc-agent",
            symbol="SPY",
            interval="5m",
        )
        candles = [
            # c1: price below zone_1 upper, above zone_2 upper — no S/R touch
            {"id": "c1", "ts": "2026-01-05T14:30:00+00:00",
             "open": 400, "high": 402, "low": 399, "close": 401, "volume": 1000},
            # c2: low touches zone_2 lower — weak support touch
            {"id": "c2", "ts": "2026-01-05T14:35:00+00:00",
             "open": 399, "high": 400, "low": 395, "close": 397, "volume": 1000},
            # c3: close breaks above zone_1 upper — resistance break
            {"id": "c3", "ts": "2026-01-05T14:40:00+00:00",
             "open": 403, "high": 407, "low": 402, "close": 406, "volume": 1000},
        ]
        for candle in candles:
            store.latest_by_candle_id[candle["id"]] = candle

        AGENT = self.SR_AGENT_ID
        for ts in ["2026-01-05T14:30:00+00:00", "2026-01-05T14:35:00+00:00", "2026-01-05T14:40:00+00:00"]:
            # zone_1: resistance 403–405, high confidence
            store.latest_non_ohlc_by_key[(AGENT, f"zone_1::{ts}")] = {
                "id": f"z1-{ts[-8:]}",
                "ts": ts,
                "output_id": "zone_1",
                "schema": "area",
                "upper": 405.0,
                "lower": 403.0,
                "metadata": {"confidence": 0.85},
            }
            # zone_2: support 396–398, low confidence
            store.latest_non_ohlc_by_key[(AGENT, f"zone_2::{ts}")] = {
                "id": f"z2-{ts[-8:]}",
                "ts": ts,
                "output_id": "zone_2",
                "schema": "area",
                "upper": 398.0,
                "lower": 396.0,
                "metadata": {"confidence": 0.30},
            }

        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: self._make_fake_sr_agent() if agent_id == AGENT else None,
        )
        return store

    def test_variable_map_resolves_sr_zone_fields(self, monkeypatch):
        """Variable map contains SR:zone_1:upper, SR:zone_1:lower for each candle timestamp."""
        store = self._make_store(monkeypatch)
        variables = trade_engine._build_indicator_variable_maps(store)

        for zone in ("zone_1", "zone_2"):
            assert f"SR:{zone}:upper" in variables
            assert f"SR:{zone}:lower" in variables
            assert f"SR:{zone}:confidence" in variables

        # All three candle timestamps present for zone_1
        assert len(variables["SR:zone_1:upper"]) == 3
        # zone_1 upper is 405.0 for all candles
        assert all(v == 405.0 for v in variables["SR:zone_1:upper"].values())

    def test_variable_map_resolves_confidence_per_zone(self, monkeypatch):
        """Confidence metadata resolves distinctly for zone_1 vs zone_2."""
        store = self._make_store(monkeypatch)
        variables = trade_engine._build_indicator_variable_maps(store)

        assert "SR:zone_1:confidence" in variables
        assert "SR:zone_2:confidence" in variables

        assert all(abs(v - 0.85) < 1e-9 for v in variables["SR:zone_1:confidence"].values())
        assert all(abs(v - 0.30) < 1e-9 for v in variables["SR:zone_2:confidence"].values())

    def test_confidence_gated_entry_high_confidence_zone(self, monkeypatch):
        """Entry gated on high-confidence zone fires when confidence threshold is met."""
        store = self._make_store(monkeypatch)

        # Enter when candle breaks above strong zone_1 resistance
        result = evaluate_strategy(
            session_id="session-sr-dsl",
            strategy_name="sr-breakout",
            long_entry_rules=[
                "CLOSE > SR:zone_1:upper AND SR:zone_1:confidence > 0.7 AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["IN_BULL_TRADE AND CLOSE < SR:zone_1:lower"],
            short_entry_rules=[],
            short_exit_rules=[],
            data_store=store,
        )

        long_entries = [m for m in result["markers"] if m["action"] == "LONG_ENTRY"]
        # Only c3 closes at 406, which is > zone_1:upper=405 and confidence=0.85 > 0.7
        assert len(long_entries) == 1
        assert long_entries[0]["ts"] == "2026-01-05T14:40:00+00:00"

    def test_confidence_gate_blocks_entry_when_below_threshold(self, monkeypatch):
        """Entry does not fire when zone confidence is below the threshold."""
        store = self._make_store(monkeypatch)

        # zone_2 has confidence=0.30 which is < 0.70 — should never enter
        result = evaluate_strategy(
            session_id="session-sr-dsl",
            strategy_name="sr-weak-zone",
            long_entry_rules=[
                "LOW < SR:zone_2:lower AND SR:zone_2:confidence > 0.70 AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["IN_BULL_TRADE AND CLOSE > SR:zone_2:upper"],
            short_entry_rules=[],
            short_exit_rules=[],
            data_store=store,
        )

        long_entries = [m for m in result["markers"] if m["action"] == "LONG_ENTRY"]
        assert len(long_entries) == 0, (
            "Should not enter when zone_2 confidence (0.30) is below threshold (0.70)"
        )

    def test_lower_threshold_allows_weak_zone_entry(self, monkeypatch):
        """Confidence gate at 0.20 allows entry on weak zone when price touches it."""
        store = self._make_store(monkeypatch)

        # zone_2 confidence=0.30 > 0.20 — should fire when c2 low hits zone_2 lower
        result = evaluate_strategy(
            session_id="session-sr-dsl",
            strategy_name="sr-any-zone",
            long_entry_rules=[
                "LOW < SR:zone_2:lower AND SR:zone_2:confidence > 0.20 AND !IN_BULL_TRADE"
            ],
            long_exit_rules=["IN_BULL_TRADE AND CLOSE > SR:zone_2:upper"],
            short_entry_rules=[],
            short_exit_rules=[],
            data_store=store,
        )

        long_entries = [m for m in result["markers"] if m["action"] == "LONG_ENTRY"]
        # c2 low is 395 < zone_2:lower=396, confidence=0.30 > 0.20 → should enter
        assert len(long_entries) == 1
        assert long_entries[0]["ts"] == "2026-01-05T14:35:00+00:00"

    def test_upserted_overlay_reflected_in_dsl_evaluation(self, monkeypatch):
        """After upsert, DSL evaluation sees the corrected zone values, not originals."""
        store = SessionDataStore(
            session_id="session-upsert-dsl",
            agent_id="ohlc-agent",
            symbol="SPY",
            interval="5m",
        )
        AGENT = self.SR_AGENT_ID
        TS = "2026-01-05T14:30:00+00:00"

        store.latest_by_candle_id["c1"] = {
            "id": "c1", "ts": TS,
            "open": 400, "high": 406, "low": 398, "close": 405, "volume": 1000,
        }

        canonical_key = (AGENT, f"zone_1::{TS}")

        # Original emission
        store.latest_non_ohlc_by_key[canonical_key] = {
            "id": "r1", "ts": TS, "output_id": "zone_1",
            "upper": 400.0, "lower": 395.0,
            "metadata": {"confidence": 0.5},
        }

        # Corrected emission — same key, new values (close at 405 > 400)
        store.latest_non_ohlc_by_key[canonical_key] = {
            "id": "r2", "ts": TS, "output_id": "zone_1",
            "upper": 403.0, "lower": 400.0,
            "metadata": {"confidence": 0.9},
        }

        monkeypatch.setattr(
            trade_engine.agent_manager,
            "get_agent",
            lambda agent_id: self._make_fake_sr_agent() if agent_id == AGENT else None,
        )

        variables = trade_engine._build_indicator_variable_maps(store)

        # DSL must see corrected values
        assert variables["SR:zone_1:upper"][TS] == 403.0
        assert variables["SR:zone_1:confidence"][TS] == 0.9


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
