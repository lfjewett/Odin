from collections import deque

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.agent_data_store import SessionDataStore
from app.agent_manager import agent_manager
from app.models import build_variable_name

TOKEN_PATTERN = re.compile(
    r"\s*(>=|<=|==|!=|>|<|AND\b|OR\b|NOT\b|\(|\)|\[|\]|,|!|[A-Za-z_][A-Za-z0-9_:\-]*|\d+(?:\.\d+)?|\+|\-|\*|\/|%)",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


@dataclass
class StrategyValidationResult:
    valid: bool
    errors: list[str]


class RuleParser:
    def __init__(self, text: str):
        self.text = self._strip_comments(text).strip()
        self.tokens = self._tokenize(self.text)
        self.index = 0

    @staticmethod
    def _strip_comments(text: str) -> str:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line
            hash_index = line.find("#")
            if hash_index != -1:
                line = line[:hash_index]
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens: list[str] = []
        cursor = 0
        while cursor < len(text):
            match = TOKEN_PATTERN.match(text, cursor)
            if not match:
                snippet = text[cursor : min(len(text), cursor + 12)]
                raise ValueError(f"Unexpected token near '{snippet}'")
            token = match.group(1)
            cursor = match.end()
            if token:
                tokens.append(token)
        return tokens

    def _peek(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of expression")
        self.index += 1
        return token

    def _accept(self, expected: str) -> bool:
        token = self._peek()
        if token is None:
            return False
        if token.upper() == expected:
            self.index += 1
            return True
        if expected == "NOT" and token == "!":
            self.index += 1
            return True
        return False

    def parse(self) -> dict[str, Any]:
        if not self.tokens:
            raise ValueError("Expression is empty")
        node = self._parse_or()
        if self._peek() is not None:
            raise ValueError(f"Unexpected token '{self._peek()}'")
        return node

    def _parse_or(self) -> dict[str, Any]:
        node = self._parse_and()
        while self._accept("OR"):
            right = self._parse_and()
            node = {"type": "or", "left": node, "right": right}
        return node

    def _parse_and(self) -> dict[str, Any]:
        node = self._parse_unary()
        while self._accept("AND"):
            right = self._parse_unary()
            node = {"type": "and", "left": node, "right": right}
        return node

    def _parse_unary(self) -> dict[str, Any]:
        if self._accept("NOT"):
            operand = self._parse_unary()
            return {"type": "not", "operand": operand}
        return self._parse_comparison()

    def _parse_comparison(self) -> dict[str, Any]:
        left = self._parse_additive()
        op = self._peek()
        if op in {">", "<", ">=", "<=", "==", "!="}:
            self._take()
            right = self._parse_additive()
            return {"type": "compare", "operator": op, "left": left, "right": right}
        return left

    def _parse_additive(self) -> dict[str, Any]:
        left = self._parse_multiplicative()
        while self._peek() in {"+", "-"}:
            op = self._take()
            right = self._parse_multiplicative()
            left = {"type": "arithmetic", "operator": op, "left": left, "right": right}
        return left

    def _parse_multiplicative(self) -> dict[str, Any]:
        left = self._parse_primary()
        while self._peek() in {"*", "/", "%"}:
            op = self._take()
            right = self._parse_primary()
            left = {"type": "arithmetic", "operator": op, "left": left, "right": right}
        return left

    def _parse_primary(self) -> dict[str, Any]:
        if self._accept("("):
            node = self._parse_or()
            if not self._accept(")"):
                raise ValueError("Missing closing ')' in expression")
            return node

        # Parse identifier, number, indexed variable, or function call
        left = self._parse_value()
        
        # If it's an identifier, check if it should be a boolean identifier
        if left["type"] == "identifier":
            name = str(left["name"]).upper()
            next_token = self._peek()
            
            # Only treat KNOWN boolean identifiers as such
            # Regular identifiers (like SMA-20:SMA) are always values, not boolean identifiers
            if name in {"IN_BULL_TRADE", "IN_BEAR_TRADE"} and (next_token is None or next_token.upper() in {"AND", "OR", ")"}):
                return {"type": "bool_identifier", "name": left["name"]}
        
        return left

    def _parse_value(self) -> dict[str, Any]:
        token = self._peek()
        if token and token == "(":
            raise ValueError(f"Unexpected '(' - missing function name or value")
        
        token = self._take()
        
        # Check if this is a function call
        if self._peek() == "(":
            # This is a function call
            self._take()  # consume '('
            func_name = token
            args: list[dict[str, Any]] = []
            
            # Parse arguments
            if self._peek() != ")":
                # Parse first argument (full expression)
                args.append(self._parse_or())
                
                # Parse remaining arguments
                while self._peek() == ",":
                    self._take()  # consume ','
                    args.append(self._parse_or())
            
            if not self._accept(")"):
                raise ValueError(f"Missing closing ')' in function call {func_name}")
            
            return {"type": "function", "name": func_name, "args": args}
        
        # Not a function call - parse as number or identifier
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return {"type": "number", "value": float(token)}
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:\-]*", token):
            # Check for array indexing [N]
            if self._peek() == "[":
                self._take()  # consume '['
                index_token = self._take()
                if not re.fullmatch(r"\d+", index_token):
                    raise ValueError(f"Lookback index must be a positive integer, got '{index_token}'")
                offset = int(index_token)
                if offset < 1:
                    raise ValueError("Lookback offset must be at least 1 (1 = previous bar)")
                if not self._accept("]"):
                    raise ValueError("Missing closing ']' in array index")
                return {"type": "indexed_variable", "name": token, "offset": offset}
            return {"type": "identifier", "name": token}
        raise ValueError(f"Invalid value token '{token}'")


def parse_rule(rule_text: str) -> dict[str, Any]:
    return RuleParser(rule_text).parse()


def validate_rule_ast(node: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def visit(current: dict[str, Any], bool_context: bool = True) -> None:
        node_type = current.get("type")

        if node_type in {"and", "or"}:
            visit(current["left"], True)
            visit(current["right"], True)
            return

        if node_type == "not":
            visit(current["operand"], True)
            return

        if node_type == "compare":
            visit(current["left"], False)
            visit(current["right"], False)
            return

        if node_type == "arithmetic":
            visit(current["left"], False)
            visit(current["right"], False)
            return

        if node_type == "function":
            for arg in current.get("args", []):
                visit(arg, False)
            return

        if node_type == "bool_identifier":
            name = str(current.get("name", "")).upper()
            if name not in {"IN_BULL_TRADE", "IN_BEAR_TRADE"}:
                errors.append(
                    f"Boolean identifier '{current.get('name')}' is not supported. Use IN_BULL_TRADE, IN_BEAR_TRADE, or comparisons."
                )
            return

        if node_type in {"identifier", "number"}:
            return

        if node_type == "indexed_variable":
            return

        if node_type == "function":
            # Already validated by recursive visit above
            return

        errors.append(f"Unsupported node type '{node_type}'")

    visit(node)
    return errors


def validate_strategy_v2(
    long_entry_rule: str,
    long_exit_rules: list[str],
    short_entry_rule: str = "",
    short_exit_rules: list[str] | None = None,
) -> StrategyValidationResult:
    errors: list[str] = []
    normalized_long_entry = long_entry_rule.strip()
    normalized_long_exits = [rule.strip() for rule in long_exit_rules if rule and rule.strip()]
    normalized_short_entry = short_entry_rule.strip()
    normalized_short_exits = [rule.strip() for rule in (short_exit_rules or []) if rule and rule.strip()]

    if not normalized_long_entry and not normalized_short_entry:
        errors.append("At least one entry rule is required (long_entry_rule or short_entry_rule)")

    if normalized_long_entry and not normalized_long_exits:
        errors.append("At least one long exit rule is required when long_entry_rule is set")

    if normalized_short_entry and not normalized_short_exits:
        errors.append("At least one short exit rule is required when short_entry_rule is set")

    if not errors and not normalized_long_exits and not normalized_short_exits:
        errors.append("At least one exit rule is required")

    if errors:
        return StrategyValidationResult(valid=False, errors=errors)

    if normalized_long_entry:
        try:
            long_entry_ast = parse_rule(normalized_long_entry)
            errors.extend([f"long_entry: {err}" for err in validate_rule_ast(long_entry_ast)])
        except ValueError as exc:
            errors.append(f"long_entry: {exc}")

    for idx, rule in enumerate(normalized_long_exits):
        try:
            ast = parse_rule(rule)
            errors.extend([f"long_exit[{idx + 1}]: {err}" for err in validate_rule_ast(ast)])
        except ValueError as exc:
            errors.append(f"long_exit[{idx + 1}]: {exc}")

    if normalized_short_entry:
        try:
            short_entry_ast = parse_rule(normalized_short_entry)
            errors.extend([f"short_entry: {err}" for err in validate_rule_ast(short_entry_ast)])
        except ValueError as exc:
            errors.append(f"short_entry: {exc}")

    for idx, rule in enumerate(normalized_short_exits):
        try:
            ast = parse_rule(rule)
            errors.extend([f"short_exit[{idx + 1}]: {err}" for err in validate_rule_ast(ast)])
        except ValueError as exc:
            errors.append(f"short_exit[{idx + 1}]: {exc}")

    return StrategyValidationResult(valid=len(errors) == 0, errors=errors)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _evaluate_value(value_node: dict[str, Any], resolver: dict[str, Any], in_bull_trade: bool, in_bear_trade: bool, history: deque | None = None) -> float:
    value_type = value_node.get("type")
    if value_type == "number":
        return float(value_node["value"])

    if value_type == "identifier":
        identifier = str(value_node["name"])
        if identifier.upper() == "IN_BULL_TRADE":
            return 1.0 if in_bull_trade else 0.0
        if identifier.upper() == "IN_BEAR_TRADE":
            return 1.0 if in_bear_trade else 0.0

        resolved = resolver.get(identifier)
        numeric = _to_float(resolved)
        if numeric is None:
            raise ValueError(f"Variable '{identifier}' is not available or non-numeric at current candle")
        return numeric

    if value_type == "indexed_variable":
        if history is None:
            raise ValueError("Lookback references require historical context")
        
        identifier = str(value_node["name"])
        offset = int(value_node["offset"])
        
        # offset=1 means previous bar, so we need to go back (offset+1) positions from current
        # history[-1] is current, history[-2] is previous (offset=1), etc.
        history_index = -(offset + 1)
        
        if abs(history_index) > len(history):
            raise ValueError(f"Lookback offset [{offset}] exceeds available history ({len(history) - 1} bars)")
        
        historical_context = history[history_index]
        
        # Handle special identifiers
        if identifier.upper() == "IN_BULL_TRADE":
            return 1.0 if historical_context.get("_in_bull_trade", False) else 0.0
        if identifier.upper() == "IN_BEAR_TRADE":
            return 1.0 if historical_context.get("_in_bear_trade", False) else 0.0
        
        resolved = historical_context.get(identifier)
        numeric = _to_float(resolved)
        if numeric is None:
            raise ValueError(f"Variable '{identifier}[{offset}]' is not available or non-numeric at offset {offset}")
        return numeric

    if value_type == "arithmetic":
        left = _evaluate_value(value_node["left"], resolver, in_bull_trade, in_bear_trade, history)
        right = _evaluate_value(value_node["right"], resolver, in_bull_trade, in_bear_trade, history)
        operator = value_node.get("operator")
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator == "/":
            if right == 0:
                raise ValueError("Division by zero")
            return left / right
        if operator == "%":
            if right == 0:
                raise ValueError("Modulo by zero")
            return left % right
        raise ValueError(f"Unsupported arithmetic operator '{operator}'")

    if value_type == "function":
        func_name = str(value_node.get("name", "")).upper()
        args = value_node.get("args", [])
        
        # Special handling for cross functions that need historical comparison
        if func_name in {"CROSSES_ABOVE", "CROSSES_BELOW", "CROSSED_ABOVE", "CROSSED_BELOW"}:
            if len(args) != 2:
                raise ValueError(f"{func_name} function requires exactly 2 arguments")
            if history is None or len(history) < 2:
                # Not enough history for cross detection - return 0 (no cross)
                return 0.0
            
            # Evaluate both arguments at current and previous bars
            current_a = _evaluate_value(args[0], resolver, in_bull_trade, in_bear_trade, history)
            current_b = _evaluate_value(args[1], resolver, in_bull_trade, in_bear_trade, history)
            
            # Get previous context
            prev_context = history[-2]
            prev_in_bull = prev_context.get("_in_bull_trade", False)
            prev_in_bear = prev_context.get("_in_bear_trade", False)
            
            # Create a temporary history for previous bar evaluation (exclude current)
            prev_history = deque(list(history)[:-1], maxlen=history.maxlen)
            
            prev_a = _evaluate_value(args[0], prev_context, prev_in_bull, prev_in_bear, prev_history)
            prev_b = _evaluate_value(args[1], prev_context, prev_in_bull, prev_in_bear, prev_history)
            
            if func_name in {"CROSSES_ABOVE", "CROSSED_ABOVE"}:
                # Cross above: current A > B and previous A <= B
                return 1.0 if (current_a > current_b and prev_a <= prev_b) else 0.0
            else:  # CROSSES_BELOW or CROSSED_BELOW
                # Cross below: current A < B and previous A >= B
                return 1.0 if (current_a < current_b and prev_a >= prev_b) else 0.0
        
        # Regular functions
        evaluated_args = [_evaluate_value(arg, resolver, in_bull_trade, in_bear_trade, history) for arg in args]
        
        if func_name == "MIN":
            if not evaluated_args:
                raise ValueError("MIN function requires at least one argument")
            return min(evaluated_args)
        if func_name == "MAX":
            if not evaluated_args:
                raise ValueError("MAX function requires at least one argument")
            return max(evaluated_args)
        if func_name == "ABS":
            if len(evaluated_args) != 1:
                raise ValueError("ABS function requires exactly one argument")
            return abs(evaluated_args[0])
        
        if func_name == "BARS_SINCE":
            if len(args) != 1:
                raise ValueError("BARS_SINCE function requires exactly 1 argument (condition)")
            if history is None or len(history) < 1:
                return 999999.0  # No history - condition never met
            
            # Check if condition is true now (at current bar)
            try:
                if _evaluate_expression(args[0], resolver, in_bull_trade, in_bear_trade, history):
                    return 0.0
            except ValueError:
                pass
            
            # Check historical bars
            for i in range(1, len(history)):
                hist_context = history[-(i + 1)]
                hist_in_bull = hist_context.get("_in_bull_trade", False)
                hist_in_bear = hist_context.get("_in_bear_trade", False)
                
                # Create history for this evaluation
                temp_history = deque(list(history)[:-(i)], maxlen=history.maxlen) if i > 0 else history
                
                try:
                    if _evaluate_expression(args[0], hist_context, hist_in_bull, hist_in_bear, temp_history):
                        return float(i)
                except ValueError:
                    # If evaluation fails, continue searching
                    continue
            
            # Condition never met in available history
            return 999999.0
        
        raise ValueError(f"Unknown function '{func_name}'")

    raise ValueError(f"Unexpected value node type '{value_type}'")


def _evaluate_expression(node: dict[str, Any], resolver: dict[str, Any], in_bull_trade: bool, in_bear_trade: bool = False, history: deque | None = None) -> bool:
    node_type = node.get("type")

    if node_type == "and":
        return _evaluate_expression(node["left"], resolver, in_bull_trade, in_bear_trade, history) and _evaluate_expression(
            node["right"], resolver, in_bull_trade, in_bear_trade, history
        )

    if node_type == "or":
        return _evaluate_expression(node["left"], resolver, in_bull_trade, in_bear_trade, history) or _evaluate_expression(
            node["right"], resolver, in_bull_trade, in_bear_trade, history
        )

    if node_type == "not":
        return not _evaluate_expression(node["operand"], resolver, in_bull_trade, in_bear_trade, history)

    if node_type == "bool_identifier":
        identifier = str(node.get("name", "")).upper()
        if identifier == "IN_BULL_TRADE":
            return in_bull_trade
        if identifier == "IN_BEAR_TRADE":
            return in_bear_trade
        return False

    if node_type == "compare":
        left = _evaluate_value(node["left"], resolver, in_bull_trade, in_bear_trade, history)
        right = _evaluate_value(node["right"], resolver, in_bull_trade, in_bear_trade, history)
        operator = node.get("operator")
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        if operator == "<=":
            return left <= right
        if operator == "==":
            return abs(left - right) < 1e-9  # Float equality with tolerance
        if operator == "!=":
            return abs(left - right) >= 1e-9  # Float inequality with tolerance
        raise ValueError(f"Unsupported comparator '{operator}'")

    # Special handling for WITHIN as an expression-level construct
    if node_type == "function":
        func_name = str(node.get("name", "")).upper()
        args = node.get("args", [])
        
        if func_name == "WITHIN":
            if len(args) != 2:
                raise ValueError("WITHIN function requires exactly 2 arguments: (condition, bars)")
            if history is None or len(history) < 2:
                return False
            
            # Second argument must evaluate to a number (bars count)
            bars_value = _evaluate_value(args[1], resolver, in_bull_trade, in_bear_trade, history)
            bars = int(bars_value)
            
            if bars < 1:
                raise ValueError("WITHIN bars count must be at least 1")
            
            # Check condition over the last N bars (excluding current)
            # History[-1] is current, [-2] is previous, etc.
            lookback_range = min(bars + 1, len(history))
            
            for i in range(1, lookback_range):
                hist_context = history[-(i + 1)]
                hist_in_bull = hist_context.get("_in_bull_trade", False)
                hist_in_bear = hist_context.get("_in_bear_trade", False)
                
                # Create history for this evaluation (all bars up to this point)
                temp_history = deque(list(history)[:-(i)], maxlen=history.maxlen) if i > 0 else history
                
                try:
                    if _evaluate_expression(args[0], hist_context, hist_in_bull, hist_in_bear, temp_history):
                        return True
                except ValueError:
                    # If evaluation fails for a historical bar, skip it
                    continue
            
            return False
        
        # For other functions used as boolean expressions, evaluate as value != 0
        result = _evaluate_value(node, resolver, in_bull_trade, in_bear_trade, history)
        return result != 0.0

    raise ValueError(f"Unsupported expression node type '{node_type}'")


def _build_indicator_variable_maps(data_store: SessionDataStore) -> dict[str, dict[str, float]]:
    variables_by_name: dict[str, dict[str, float]] = {}

    indicator_agent_ids = {agent_id for (agent_id, _) in data_store.latest_non_ohlc_by_key.keys()}

    for agent_id in indicator_agent_ids:
        agent = agent_manager.get_agent(agent_id)
        if not agent:
            continue

        output_configs: list[tuple[str, str | None, str]] = []
        for output in agent.config.outputs:
            schema = output.get("schema", "line")
            output_id = output.get("output_id")

            if schema in {"line", "histogram", "forecast"}:
                variable_name = build_variable_name(agent.config.agent_name, output)
                output_configs.append((variable_name, str(output_id) if output_id else None, "value"))
                variables_by_name[variable_name] = {}
                continue

            if schema == "band":
                base_name = build_variable_name(agent.config.agent_name, output)
                for field in ("upper", "lower", "center"):
                    variable_name = f"{base_name}:{field}"
                    output_configs.append((variable_name, str(output_id) if output_id else None, field))
                    variables_by_name[variable_name] = {}
                continue

            if schema == "area":
                base_name = build_variable_name(agent.config.agent_name, output)
                for field in ("upper", "lower"):
                    variable_name = f"{base_name}:{field}"
                    output_configs.append((variable_name, str(output_id) if output_id else None, field))
                    variables_by_name[variable_name] = {}
                continue

        if not output_configs:
            continue

        has_multiple_outputs = len(output_configs) > 1

        for (source_agent_id, _record_id), record in data_store.latest_non_ohlc_by_key.items():
            if source_agent_id != agent_id:
                continue

            ts = str(record.get("ts", "")).strip()
            if not ts:
                continue

            record_output_id_raw = record.get("output_id")
            record_output_id = str(record_output_id_raw) if record_output_id_raw is not None else None

            for variable_name, configured_output_id, field_name in output_configs:
                if configured_output_id and record_output_id and configured_output_id != record_output_id:
                    continue
                if has_multiple_outputs and configured_output_id and record_output_id is None:
                    continue
                source_value = record.get(field_name)
                source_numeric = _to_float(source_value)
                if source_numeric is None:
                    if field_name != "value":
                        continue
                    source_numeric = _to_float(record.get("value"))
                    if source_numeric is None:
                        continue
                variables_by_name[variable_name][ts] = source_numeric

    return variables_by_name


def evaluate_strategy(
    session_id: str,
    strategy_name: str,
    long_entry_rule: str,
    long_exit_rules: list[str],
    data_store: SessionDataStore,
    short_entry_rule: str = "",
    short_exit_rules: list[str] | None = None,
) -> dict[str, Any]:
    normalized_long_entry = long_entry_rule.strip()
    normalized_long_exits = [rule.strip() for rule in long_exit_rules if rule and rule.strip()]
    normalized_short_entry = short_entry_rule.strip()
    normalized_short_exits = [rule.strip() for rule in (short_exit_rules or []) if rule and rule.strip()]

    validation = validate_strategy_v2(
        long_entry_rule=normalized_long_entry,
        long_exit_rules=normalized_long_exits,
        short_entry_rule=normalized_short_entry,
        short_exit_rules=normalized_short_exits,
    )
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))

    long_entry_ast = parse_rule(normalized_long_entry) if normalized_long_entry else None
    long_exit_asts = [parse_rule(rule) for rule in normalized_long_exits]
    short_entry_ast = parse_rule(normalized_short_entry) if normalized_short_entry else None
    short_exit_asts = [parse_rule(rule) for rule in normalized_short_exits]

    candles = list(data_store.latest_by_candle_id.values())
    candles.sort(key=lambda candle: candle.get("ts", ""))

    indicator_values = _build_indicator_variable_maps(data_store)

    markers: list[dict[str, Any]] = []
    in_bull_trade = False
    in_bear_trade = False
    starting_capital = 10000.0
    lot_size = 100
    cash = starting_capital
    shares = 0
    entry_price = 0.0
    entry_shares = 0
    closed_trade_pnls: list[float] = []
    closed_trade_returns: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    
    # Historical context for lookback support (keep last 200 bars)
    max_history = 200
    context_history: deque[dict[str, Any]] = deque(maxlen=max_history)

    for candle in candles:
        candle_id = str(candle.get("id", ""))
        if not candle_id:
            continue
        candle_ts = str(candle.get("ts", "")).strip()

        open_price = float(candle.get("open") or 0)
        high_price = float(candle.get("high") or 0)
        low_price = float(candle.get("low") or 0)
        close_price = float(candle.get("close") or 0)
        volume = float(candle.get("volume") or 0)
        
        # Built-in candle variables
        body = abs(close_price - open_price)
        range_val = high_price - low_price
        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price
        
        # Time-based variables (Eastern Time for market hours)
        try:
            dt_utc = datetime.fromisoformat(candle_ts.replace('Z', '+00:00'))
            dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
            hour_et = dt_et.hour
            minute_et = dt_et.minute
            time_hhmm = hour_et * 100 + minute_et
        except (ValueError, KeyError, OSError):
            # If timestamp parsing fails, set to 0
            hour_et = 0
            minute_et = 0
            time_hhmm = 0
        
        context: dict[str, Any] = {
            "OPEN": open_price,
            "HIGH": high_price,
            "LOW": low_price,
            "CLOSE": close_price,
            "VOLUME": volume,
            "BODY": body,
            "RANGE": range_val,
            "UPPER_WICK": upper_wick,
            "LOWER_WICK": lower_wick,
            "TIME": time_hhmm,
            "HOUR": hour_et,
            "MINUTE": minute_et,
        }

        for variable_name, values_by_candle_id in indicator_values.items():
            context[variable_name] = values_by_candle_id.get(candle_ts) or values_by_candle_id.get(candle_id)

        # Store state in context for historical lookback
        context["_in_bull_trade"] = in_bull_trade
        context["_in_bear_trade"] = in_bear_trade
        
        # Add current context to history
        context_history.append(context.copy())

        try:
            long_entry_match = (
                _evaluate_expression(long_entry_ast, context, in_bull_trade, in_bear_trade, context_history)
                if long_entry_ast
                else False
            )
            long_exit_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history)
                for ast in long_exit_asts
            )
            short_entry_match = (
                _evaluate_expression(short_entry_ast, context, in_bull_trade, in_bear_trade, context_history)
                if short_entry_ast
                else False
            )
            short_exit_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history)
                for ast in short_exit_asts
            )
        except ValueError as exc:
            error_msg = str(exc)
            if "is not available or non-numeric" in error_msg:
                continue
            if "exceeds available history" in error_msg:
                # Skip candles where we don't have enough history yet
                continue
            if "Not enough history for cross detection" in error_msg:
                # Skip until we have enough history
                continue
            raise

        ts = str(candle.get("ts") or _now_iso())
        fill_price = float(candle.get("close") or 0)

        if not in_bull_trade and not in_bear_trade and long_entry_match:
            in_bull_trade = True
            share_cost = fill_price
            quantity = int(cash // share_cost) if share_cost > 0 else 0

            logger.info(
                "[TradeEngine] LONG ENTRY SIGNAL ts=%s close=%.2f cash=%.2f quantity=%d",
                ts, fill_price, cash, quantity
            )
            
            if quantity > 0:
                cash -= quantity * fill_price
                shares = quantity
                entry_price = fill_price
                entry_shares = quantity
                logger.info(
                    "[TradeEngine] LONG ENTRY EXECUTED shares=%d entry_price=%.2f cost=%.2f remaining_cash=%.2f",
                    entry_shares, entry_price, entry_shares * entry_price, cash
                )
            else:
                logger.warning(
                    "[TradeEngine] LONG ENTRY SKIPPED - insufficient cash (cash=%.2f, share_cost=%.2f)",
                    cash, share_cost
                )
            markers.append(
                {
                    "id": f"trade-long-entry-{session_id}-{candle_id}",
                    "candle_id": candle_id,
                    "ts": ts,
                    "title": "LONG ENTRY",
                    "description": f"{strategy_name} long entry",
                    "severity": "info",
                    "action": "LONG_ENTRY",
                }
            )
        elif not in_bull_trade and not in_bear_trade and short_entry_match:
            in_bear_trade = True
            share_cost = fill_price
            quantity = int(cash // share_cost) if share_cost > 0 else 0

            logger.info(
                "[TradeEngine] SHORT ENTRY SIGNAL ts=%s close=%.2f cash=%.2f quantity=%d",
                ts, fill_price, cash, quantity
            )

            if quantity > 0:
                cash += quantity * fill_price
                shares = -quantity
                entry_price = fill_price
                entry_shares = quantity
                logger.info(
                    "[TradeEngine] SHORT ENTRY EXECUTED shares=%d entry_price=%.2f proceeds=%.2f cash=%.2f",
                    entry_shares, entry_price, entry_shares * entry_price, cash
                )
            else:
                logger.warning(
                    "[TradeEngine] SHORT ENTRY SKIPPED - insufficient buying power (cash=%.2f, share_cost=%.2f)",
                    cash, share_cost
                )

            markers.append(
                {
                    "id": f"trade-short-entry-{session_id}-{candle_id}",
                    "candle_id": candle_id,
                    "ts": ts,
                    "title": "SHORT ENTRY",
                    "description": f"{strategy_name} short entry",
                    "severity": "info",
                    "action": "SHORT_ENTRY",
                }
            )
        elif long_exit_match and in_bull_trade:
            in_bull_trade = False
            logger.info(
                "[TradeEngine] LONG EXIT SIGNAL ts=%s shares=%d",
                ts, shares
            )
            if shares > 0:
                cash += shares * fill_price
                pnl = (fill_price - entry_price) * entry_shares
                invested = entry_price * entry_shares
                if invested > 0:
                    closed_trade_returns.append(pnl / invested)
                closed_trade_pnls.append(pnl)
                logger.info(
                    "[TradeEngine] LONG EXIT EXECUTED shares=%d exit_price=%.2f entry_price=%.2f pnl=%.2f new_cash=%.2f",
                    entry_shares, fill_price, entry_price, pnl, cash
                )
                shares = 0
                entry_price = 0.0
                entry_shares = 0
            else:
                logger.warning(
                    "[TradeEngine] LONG EXIT SKIPPED - no shares held (entry was likely skipped)"
                )
            markers.append(
                {
                    "id": f"trade-long-exit-{session_id}-{candle_id}",
                    "candle_id": candle_id,
                    "ts": ts,
                    "title": "LONG EXIT",
                    "description": f"{strategy_name} long exit",
                    "severity": "warning",
                    "action": "LONG_EXIT",
                }
            )
        elif short_exit_match and in_bear_trade:
            in_bear_trade = False
            logger.info(
                "[TradeEngine] SHORT EXIT SIGNAL ts=%s shares=%d",
                ts, shares
            )
            if shares < 0 and entry_shares > 0:
                cash -= entry_shares * fill_price
                pnl = (entry_price - fill_price) * entry_shares
                invested = entry_price * entry_shares
                if invested > 0:
                    closed_trade_returns.append(pnl / invested)
                closed_trade_pnls.append(pnl)
                logger.info(
                    "[TradeEngine] SHORT EXIT EXECUTED shares=%d exit_price=%.2f entry_price=%.2f pnl=%.2f cash=%.2f",
                    entry_shares, fill_price, entry_price, pnl, cash
                )
                shares = 0
                entry_price = 0.0
                entry_shares = 0
            else:
                logger.warning(
                    "[TradeEngine] SHORT EXIT SKIPPED - no short shares held"
                )
            markers.append(
                {
                    "id": f"trade-short-exit-{session_id}-{candle_id}",
                    "candle_id": candle_id,
                    "ts": ts,
                    "title": "SHORT EXIT",
                    "description": f"{strategy_name} short exit",
                    "severity": "warning",
                    "action": "SHORT_EXIT",
                }
            )

        equity_curve.append({
            "ts": ts,
            "equity": cash + shares * fill_price,
        })

    final_equity = equity_curve[-1]["equity"] if equity_curve else starting_capital
    total_pl = final_equity - starting_capital
    total_trades = len(closed_trade_pnls)
    wins = [pnl for pnl in closed_trade_pnls if pnl > 0]
    losses = [pnl for pnl in closed_trade_pnls if pnl < 0]
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

    peak = starting_capital
    max_drawdown = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    max_loss = min(losses) if losses else 0.0

    if len(closed_trade_returns) > 0:
        mean_return = sum(closed_trade_returns) / len(closed_trade_returns)
    else:
        mean_return = 0.0

    if len(closed_trade_returns) > 1:
        variance = sum((value - mean_return) ** 2 for value in closed_trade_returns) / (len(closed_trade_returns) - 1)
    else:
        variance = 0.0
    std_dev = variance ** 0.5
    sharpe_ratio = (mean_return / std_dev * (len(closed_trade_returns) ** 0.5)) if std_dev > 0 else 0.0

    result = {
        "session_id": session_id,
        "strategy_name": strategy_name,
        "long_entry_rule": normalized_long_entry,
        "long_exit_rules": normalized_long_exits,
        "short_entry_rule": normalized_short_entry,
        "short_exit_rules": normalized_short_exits,
        "markers": markers,
        "marker_count": len(markers),
        "performance": {
            "starting_capital": starting_capital,
            "lot_size": lot_size,
            "final_equity": final_equity,
            "total_pl": total_pl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
            "average_win": average_win,
            "average_loss": average_loss,
            "max_loss": max_loss,
            "equity_curve": equity_curve,
        },
    }

    logger.info(
        "[TradeEngine] Computed performance session=%s strategy=%s markers=%s trades=%s total_pl=%.2f win_rate=%.2f max_dd=%.2f sharpe=%.3f curve_points=%s",
        session_id,
        strategy_name,
        len(markers),
        total_trades,
        total_pl,
        win_rate,
        max_drawdown,
        sharpe_ratio,
        len(equity_curve),
    )

    return result
