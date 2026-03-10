#!/usr/bin/env python3
"""Quick DSL validation tests for the critical bug fix"""

import sys
sys.path.insert(0, '/Users/ljewett/git/home/github/Odin/backend')

from app.trade_engine import validate_strategy_v2


def validate_long_only(entry_rule: str, exit_rule: str):
    return validate_strategy_v2(
        long_entry_rule=entry_rule,
        long_exit_rules=[exit_rule],
        short_entry_rule="",
        short_exit_rules=[],
    )

def test_sma_crossover():
    """Test the critical failing case"""
    print("Test 1: SMA crossover with indicator variables")
    result = validate_long_only(
        "SMA-20:SMA > SMA-50:SMA AND CLOSE < SMA-20:SMA AND !IN_BULL_TRADE",
        "SMA-20:SMA < SMA-50:SMA AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: SMA crossover strategy validates correctly\n")
        return True
    else:
        print("✗ FAIL: Validation errors:")
        for err in result.errors:
            print(f"  - {err}")
        print()
        return False

def test_lookback():
    """Test lookback references"""
    print("Test 2: Lookback references")
    result = validate_long_only(
        "CLOSE > CLOSE[1] AND CLOSE[1] > CLOSE[2] AND !IN_BULL_TRADE",
        "CLOSE < CLOSE[1] AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: Lookback references work\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

def test_math_and_functions():
    """Test math operators with functions"""
    print("Test 3: Math operators with functions")
    result = validate_long_only(
        "CLOSE < MIN(LOW[1], LOW[2], LOW[3]) AND !IN_BULL_TRADE",
        "CLOSE > MAX(HIGH[1], HIGH[2]) AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: MIN/MAX functions with lookback work\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

def test_time_filtering():
    """Test time filtering"""
    print("Test 4: Time filtering")
    result = validate_long_only(
        "TIME >= 1030 AND TIME < 1400 AND CLOSE < OPEN AND !IN_BULL_TRADE",
        "CLOSE > OPEN AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: Time filtering works\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

def test_candle_variables():
    """Test built-in candle variables"""
    print("Test 5: Built-in candle variables")
    result = validate_long_only(
        "BODY > 2 AND LOWER_WICK < BODY * 0.2 AND !IN_BULL_TRADE",
        "BODY < 0.5 AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: Built-in candle variables work\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

def test_cross_functions():
    """Test cross detection functions"""
    print("Test 6: Golden cross strategy with CROSSES_ABOVE")
    result = validate_long_only(
        "CROSSES_ABOVE(SMA-20:SMA, SMA-50:SMA) > 0 AND !IN_BULL_TRADE",
        "CROSSES_BELOW(SMA-20:SMA, SMA-50:SMA) > 0 AND IN_BULL_TRADE"
    )
    if result.valid:
        print("✓ PASS: Cross detection functions work\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

def test_comments_support():
    """Test # comments in rules"""
    print("Test 7: Comment support")
    result = validate_long_only(
        "SMA-20:SMA > SMA-50:SMA AND !IN_BULL_TRADE  # long trend filter",
        "CLOSE > SMA-20:SMA AND IN_BULL_TRADE # take profit"
    )
    if result.valid:
        print("✓ PASS: # comments are supported\n")
        return True
    else:
        print("✗ FAIL: " + str(result.errors) + "\n")
        return False

if __name__ == "__main__":
    print("="*60)
    print("DSL Validation Test Suite")
    print("="*60 + "\n")
    
    results = [
        test_sma_crossover(),
        test_lookback(),
        test_math_and_functions(),
        test_time_filtering(),
        test_candle_variables(),
        test_cross_functions(),
        test_comments_support(),
    ]
    
    print("="*60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    if passed == total:
        print("✓ All tests PASSED!")
    else:
        print(f"✗ {total - passed} test(s) FAILED")
    print("="*60)
    
    sys.exit(0 if passed == total else 1)
