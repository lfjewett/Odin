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

import pytest
from app.trade_engine import RuleParser, validate_rule_ast, parse_rule, validate_strategy_v2


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
