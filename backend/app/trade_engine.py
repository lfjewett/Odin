from collections import deque

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.agent_data_store import SessionDataStore
from app.agent_manager import agent_manager
from app.models import (
    build_area_field_legacy_variable_name,
    build_area_field_variable_name,
    build_area_metadata_legacy_variable_name,
    build_area_metadata_variable_name,
    build_variable_name,
)

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

        if node_type == "identifier":
            if bool_context:
                errors.append(
                    f"Identifier '{current.get('name')}' is not supported as a boolean expression. "
                    "Use a comparison (e.g. VAR > 0) or IN_BULL_TRADE / IN_BEAR_TRADE."
                )
            return

        if node_type == "number":
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
    long_entry_rules: list[str] | None = None,
    long_exit_rules: list[str] | None = None,
    short_entry_rules: list[str] | None = None,
    short_exit_rules: list[str] | None = None,
    # Backward compatibility: accept single-string format
    long_entry_rule: str = "",
    short_entry_rule: str = "",
) -> StrategyValidationResult:
    errors: list[str] = []
    
    # Support both new array format and old single-string format
    if long_entry_rule and not long_entry_rules:
        long_entry_rules = [long_entry_rule]
    if short_entry_rule and not short_entry_rules:
        short_entry_rules = [short_entry_rule]
    
    normalized_long_entries = [rule.strip() for rule in (long_entry_rules or []) if rule and rule.strip()]
    normalized_long_exits = [rule.strip() for rule in (long_exit_rules or []) if rule and rule.strip()]
    normalized_short_entries = [rule.strip() for rule in (short_entry_rules or []) if rule and rule.strip()]
    normalized_short_exits = [rule.strip() for rule in (short_exit_rules or []) if rule and rule.strip()]

    if not normalized_long_entries and not normalized_short_entries:
        errors.append("At least one entry rule is required (long_entry_rules or short_entry_rules)")

    if normalized_long_entries and not normalized_long_exits:
        errors.append("At least one long exit rule is required when long_entry_rules are set")

    if normalized_short_entries and not normalized_short_exits:
        errors.append("At least one short exit rule is required when short_entry_rules are set")

    if not errors and not normalized_long_exits and not normalized_short_exits:
        errors.append("At least one exit rule is required")

    if errors:
        return StrategyValidationResult(valid=False, errors=errors)

    for idx, rule in enumerate(normalized_long_entries):
        try:
            long_entry_ast = parse_rule(rule)
            errors.extend([f"long_entry[{idx + 1}]: {err}" for err in validate_rule_ast(long_entry_ast)])
        except ValueError as exc:
            errors.append(f"long_entry[{idx + 1}]: {exc}")

    for idx, rule in enumerate(normalized_long_exits):
        try:
            ast = parse_rule(rule)
            errors.extend([f"long_exit[{idx + 1}]: {err}" for err in validate_rule_ast(ast)])
        except ValueError as exc:
            errors.append(f"long_exit[{idx + 1}]: {exc}")

    for idx, rule in enumerate(normalized_short_entries):
        try:
            short_entry_ast = parse_rule(rule)
            errors.extend([f"short_entry[{idx + 1}]: {err}" for err in validate_rule_ast(short_entry_ast)])
        except ValueError as exc:
            errors.append(f"short_entry[{idx + 1}]: {exc}")

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


def _resolve_position_value(identifier: str, resolver: dict[str, Any], position_side: str | None) -> float | None:
    side = (position_side or "").lower()
    if side not in {"long", "short"}:
        return None

    normalized = identifier.upper()
    if side == "long":
        key_map = {
            "SHARES_HELD": "_long_shares_held",
            "ENTRY_PRICE": "_long_entry_price",
            "UNREALIZED_PNL": "_long_unrealized_pnl",
        }
    else:
        key_map = {
            "SHARES_HELD": "_short_shares_held",
            "ENTRY_PRICE": "_short_entry_price",
            "UNREALIZED_PNL": "_short_unrealized_pnl",
        }

    hidden_key = key_map.get(normalized)
    if hidden_key is None:
        return None

    numeric = _to_float(resolver.get(hidden_key))
    return numeric if numeric is not None else 0.0


def _evaluate_value(
    value_node: dict[str, Any],
    resolver: dict[str, Any],
    in_bull_trade: bool,
    in_bear_trade: bool,
    history: deque | None = None,
    position_side: str | None = None,
) -> float:
    value_type = value_node.get("type")
    if value_type == "number":
        return float(value_node["value"])

    if value_type == "identifier":
        identifier = str(value_node["name"])
        if identifier.upper() == "IN_BULL_TRADE":
            return 1.0 if in_bull_trade else 0.0
        if identifier.upper() == "IN_BEAR_TRADE":
            return 1.0 if in_bear_trade else 0.0

        position_value = _resolve_position_value(identifier, resolver, position_side)
        if position_value is not None:
            return position_value

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

        position_value = _resolve_position_value(identifier, historical_context, position_side)
        if position_value is not None:
            return position_value
        
        resolved = historical_context.get(identifier)
        numeric = _to_float(resolved)
        if numeric is None:
            raise ValueError(f"Variable '{identifier}[{offset}]' is not available or non-numeric at offset {offset}")
        return numeric

    if value_type == "arithmetic":
        left = _evaluate_value(value_node["left"], resolver, in_bull_trade, in_bear_trade, history, position_side)
        right = _evaluate_value(value_node["right"], resolver, in_bull_trade, in_bear_trade, history, position_side)
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
            current_a = _evaluate_value(args[0], resolver, in_bull_trade, in_bear_trade, history, position_side)
            current_b = _evaluate_value(args[1], resolver, in_bull_trade, in_bear_trade, history, position_side)
            
            # Get previous context
            prev_context = history[-2]
            prev_in_bull = prev_context.get("_in_bull_trade", False)
            prev_in_bear = prev_context.get("_in_bear_trade", False)
            
            # Create a temporary history for previous bar evaluation (exclude current)
            prev_history = deque(list(history)[:-1], maxlen=history.maxlen)
            
            prev_a = _evaluate_value(args[0], prev_context, prev_in_bull, prev_in_bear, prev_history, position_side)
            prev_b = _evaluate_value(args[1], prev_context, prev_in_bull, prev_in_bear, prev_history, position_side)
            
            if func_name in {"CROSSES_ABOVE", "CROSSED_ABOVE"}:
                # Cross above: current A > B and previous A <= B
                return 1.0 if (current_a > current_b and prev_a <= prev_b) else 0.0
            else:  # CROSSES_BELOW or CROSSED_BELOW
                # Cross below: current A < B and previous A >= B
                return 1.0 if (current_a < current_b and prev_a >= prev_b) else 0.0
        
        # Functions that take expression (boolean) arguments must be dispatched
        # before evaluated_args — passing a compare/bool node to _evaluate_value fails.
        if func_name == "BARS_SINCE":
            if len(args) != 1:
                raise ValueError("BARS_SINCE function requires exactly 1 argument (condition)")
            if history is None or len(history) < 1:
                return 999999.0  # No history - condition never met
            
            # Check if condition is true now (at current bar)
            try:
                if _evaluate_expression(args[0], resolver, in_bull_trade, in_bear_trade, history, position_side):
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
                    if _evaluate_expression(args[0], hist_context, hist_in_bull, hist_in_bear, temp_history, position_side):
                        return float(i)
                except ValueError:
                    # If evaluation fails, continue searching
                    continue
            
            # Condition never met in available history
            return 999999.0

        if func_name in {"LONG_ENTRIES_SINCE", "SHORT_ENTRIES_SINCE"}:
            if len(args) != 1:
                raise ValueError(f"{func_name} requires exactly 1 argument (condition)")
            if history is None or len(history) < 2:
                return 999999.0

            entry_key = "_long_entry_taken" if func_name == "LONG_ENTRIES_SINCE" else "_short_entry_taken"
            history_list = list(history)

            # Walk backward to find the most recent false→true transition of the condition
            trigger_bar_index = None
            for i in range(len(history_list) - 1, 0, -1):
                curr = history_list[i]
                prev = history_list[i - 1]
                curr_in_bull = curr.get("_in_bull_trade", False)
                curr_in_bear = curr.get("_in_bear_trade", False)
                prev_in_bull = prev.get("_in_bull_trade", False)
                prev_in_bear = prev.get("_in_bear_trade", False)
                try:
                    temp_curr = deque(history_list[:i + 1], maxlen=history.maxlen)
                    temp_prev = deque(history_list[:i], maxlen=history.maxlen)
                    curr_val = _evaluate_expression(args[0], curr, curr_in_bull, curr_in_bear, temp_curr, position_side)
                    prev_val = _evaluate_expression(args[0], prev, prev_in_bull, prev_in_bear, temp_prev, position_side)
                except ValueError:
                    continue
                if curr_val and not prev_val:
                    trigger_bar_index = i
                    break

            if trigger_bar_index is None:
                return 999999.0

            # Count entries taken from the trigger bar onward (inclusive)
            count = sum(1 for bar in history_list[trigger_bar_index:] if bar.get(entry_key, False))
            return float(count)

        # Regular numeric functions (args are all numeric values)
        evaluated_args = [
            _evaluate_value(arg, resolver, in_bull_trade, in_bear_trade, history, position_side)
            for arg in args
        ]

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

        raise ValueError(f"Unknown function '{func_name}'")

    raise ValueError(f"Unexpected value node type '{value_type}'")


def _evaluate_expression(
    node: dict[str, Any],
    resolver: dict[str, Any],
    in_bull_trade: bool,
    in_bear_trade: bool = False,
    history: deque | None = None,
    position_side: str | None = None,
) -> bool:
    node_type = node.get("type")

    if node_type == "and":
        return _evaluate_expression(node["left"], resolver, in_bull_trade, in_bear_trade, history, position_side) and _evaluate_expression(
            node["right"], resolver, in_bull_trade, in_bear_trade, history, position_side
        )

    if node_type == "or":
        return _evaluate_expression(node["left"], resolver, in_bull_trade, in_bear_trade, history, position_side) or _evaluate_expression(
            node["right"], resolver, in_bull_trade, in_bear_trade, history, position_side
        )

    if node_type == "not":
        return not _evaluate_expression(node["operand"], resolver, in_bull_trade, in_bear_trade, history, position_side)

    if node_type == "bool_identifier":
        identifier = str(node.get("name", "")).upper()
        if identifier == "IN_BULL_TRADE":
            return in_bull_trade
        if identifier == "IN_BEAR_TRADE":
            return in_bear_trade
        return False

    if node_type == "compare":
        left = _evaluate_value(node["left"], resolver, in_bull_trade, in_bear_trade, history, position_side)
        right = _evaluate_value(node["right"], resolver, in_bull_trade, in_bear_trade, history, position_side)
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

    if node_type == "identifier":
        identifier = str(node.get("name", ""))
        raise ValueError(
            f"Identifier '{identifier}' is not supported as a boolean expression. "
            "Use a comparison (e.g. VAR > 0) or IN_BULL_TRADE / IN_BEAR_TRADE."
        )

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
            bars_value = _evaluate_value(args[1], resolver, in_bull_trade, in_bear_trade, history, position_side)
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
                    if _evaluate_expression(args[0], hist_context, hist_in_bull, hist_in_bear, temp_history, position_side):
                        return True
                except ValueError:
                    # If evaluation fails for a historical bar, skip it
                    continue
            
            return False
        
        # For other functions used as boolean expressions, evaluate as value != 0
        result = _evaluate_value(node, resolver, in_bull_trade, in_bear_trade, history, position_side)
        return result != 0.0

    raise ValueError(f"Unsupported expression node type '{node_type}'")


def _build_indicator_variable_maps(data_store: SessionDataStore) -> dict[str, dict[str, float]]:
    variables_by_name: dict[str, dict[str, float]] = {}

    indicator_agent_ids = {agent_id for (agent_id, _) in data_store.latest_non_ohlc_by_key.keys()}

    for agent_id in indicator_agent_ids:
        agent = agent_manager.get_agent(agent_id)
        if not agent:
            continue

        records_for_agent = [
            record
            for (source_agent_id, _storage_key), record in data_store.latest_non_ohlc_by_key.items()
            if source_agent_id == agent_id
        ]

        configured_output_ids = {
            str(output.get("output_id"))
            for output in agent.config.outputs
            if output.get("output_id") is not None
        }
        requires_output_id_disambiguation = len(configured_output_ids) > 1

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
                for field in ("upper", "lower"):
                    variable_name = build_area_field_variable_name(
                        agent.config.agent_name,
                        output,
                        field,
                    )
                    output_configs.append((variable_name, str(output_id) if output_id else None, field))
                    variables_by_name[variable_name] = {}

                    legacy_variable_name = build_area_field_legacy_variable_name(
                        agent.config.agent_name,
                        output,
                        field,
                    )
                    if legacy_variable_name != variable_name:
                        output_configs.append((legacy_variable_name, str(output_id) if output_id else None, field))
                        variables_by_name[legacy_variable_name] = {}

                metadata_keys: set[str] = set()
                for record in records_for_agent:
                    record_output_id = record.get("output_id")
                    if output_id and record_output_id and str(record_output_id) != str(output_id):
                        continue

                    metadata = record.get("metadata")
                    if not isinstance(metadata, dict):
                        continue

                    for metadata_key, metadata_value in metadata.items():
                        if isinstance(metadata_value, bool):
                            continue
                        if isinstance(metadata_value, (int, float)):
                            metadata_keys.add(str(metadata_key))

                for metadata_key in sorted(metadata_keys):
                    variable_name = build_area_metadata_variable_name(
                        agent.config.agent_name,
                        output,
                        metadata_key,
                    )
                    output_configs.append((variable_name, str(output_id) if output_id else None, f"metadata:{metadata_key}"))
                    variables_by_name[variable_name] = {}

                    legacy_variable_name = build_area_metadata_legacy_variable_name(
                        agent.config.agent_name,
                        output,
                        metadata_key,
                    )
                    if legacy_variable_name != variable_name:
                        output_configs.append((legacy_variable_name, str(output_id) if output_id else None, f"metadata:{metadata_key}"))
                        variables_by_name[legacy_variable_name] = {}
                continue

        if not output_configs:
            continue

        for record in records_for_agent:

            ts = str(record.get("ts", "")).strip()
            if not ts:
                continue

            record_output_id_raw = record.get("output_id")
            record_output_id = str(record_output_id_raw) if record_output_id_raw is not None else None

            for variable_name, configured_output_id, field_name in output_configs:
                if configured_output_id and record_output_id and configured_output_id != record_output_id:
                    continue
                if requires_output_id_disambiguation and configured_output_id and record_output_id is None:
                    continue

                source_value = None
                if field_name.startswith("metadata:"):
                    metadata_field = field_name.split(":", 1)[1]
                    metadata_obj = record.get("metadata")
                    if isinstance(metadata_obj, dict):
                        source_value = metadata_obj.get(metadata_field)
                else:
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


def _build_position_context(
    *,
    close_price: float,
    long_shares: int,
    long_entry_price: float,
    long_entry_shares: int,
    short_shares: int,
    short_entry_price: float,
    short_entry_shares: int,
) -> dict[str, float]:
    in_long = long_shares > 0 and long_entry_shares > 0 and long_entry_price > 0
    in_short = short_shares > 0 and short_entry_shares > 0 and short_entry_price > 0

    long_unrealized_pnl = (close_price - long_entry_price) * long_entry_shares if in_long else 0.0
    short_unrealized_pnl = (short_entry_price - close_price) * short_entry_shares if in_short else 0.0

    public_shares_held = 0.0
    public_entry_price = 0.0
    public_unrealized_pnl = 0.0
    if in_long and not in_short:
        public_shares_held = float(long_shares)
        public_entry_price = float(long_entry_price)
        public_unrealized_pnl = float(long_unrealized_pnl)
    elif in_short and not in_long:
        public_shares_held = float(-short_shares)
        public_entry_price = float(short_entry_price)
        public_unrealized_pnl = float(short_unrealized_pnl)

    return {
        "SHARES_HELD": public_shares_held,
        "ENTRY_PRICE": public_entry_price,
        "UNREALIZED_PNL": public_unrealized_pnl,
        "_long_shares_held": float(long_shares) if in_long else 0.0,
        "_long_entry_price": float(long_entry_price) if in_long else 0.0,
        "_long_unrealized_pnl": float(long_unrealized_pnl),
        "_short_shares_held": float(-short_shares) if in_short else 0.0,
        "_short_entry_price": float(short_entry_price) if in_short else 0.0,
        "_short_unrealized_pnl": float(short_unrealized_pnl),
    }


def _compute_equity(cash: float, long_shares: int, short_shares: int, fill_price: float) -> float:
    return cash + (long_shares * fill_price) - (short_shares * fill_price)


def _is_boolean_expression_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "")
    if node_type in {"and", "or", "not", "compare", "bool_identifier"}:
        return True
    if node_type == "function":
        func_name = str(node.get("name") or "").upper()
        return func_name == "WITHIN"
    return False


def evaluate_research_expression(
    session_id: str,
    expression: str,
    output_schema: str,
    data_store: SessionDataStore,
) -> dict[str, Any]:
    normalized_expression = expression.strip()
    if not normalized_expression:
        raise ValueError("Expression is required")

    normalized_schema = output_schema.strip().lower()
    if normalized_schema not in {"line", "area", "histogram"}:
        raise ValueError("Unsupported output schema. Expected one of: line, area, histogram")

    ast = parse_rule(normalized_expression)
    candles = list(data_store.latest_by_candle_id.values())
    candles.sort(key=lambda candle: candle.get("ts", ""))

    indicator_values = _build_indicator_variable_maps(data_store)
    records: list[dict[str, Any]] = []

    max_history = 200
    context_history: deque[dict[str, Any]] = deque(maxlen=max_history)
    daily_pnl = 0.0
    current_day_et: Any = None

    for candle in candles:
        candle_id = str(candle.get("id", "")).strip()
        candle_ts = str(candle.get("ts", "")).strip()
        if not candle_id or not candle_ts:
            continue

        open_price = float(candle.get("open") or 0)
        high_price = float(candle.get("high") or 0)
        low_price = float(candle.get("low") or 0)
        close_price = float(candle.get("close") or 0)
        volume = float(candle.get("volume") or 0)

        body = abs(close_price - open_price)
        range_val = high_price - low_price
        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price

        candle_date_et = None
        try:
            dt_utc = datetime.fromisoformat(candle_ts.replace('Z', '+00:00'))
            dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
            hour_et = dt_et.hour
            minute_et = dt_et.minute
            time_hhmm = hour_et * 100 + minute_et
            candle_date_et = dt_et.date()
        except (ValueError, KeyError, OSError):
            hour_et = 0
            minute_et = 0
            time_hhmm = 0

        if candle_date_et is not None:
            if current_day_et is None:
                current_day_et = candle_date_et
            elif candle_date_et != current_day_et:
                daily_pnl = 0.0
                current_day_et = candle_date_et

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
            "DAILY_PNL": daily_pnl,
        }
        context.update(
            _build_position_context(
                close_price=close_price,
                long_shares=0,
                long_entry_price=0.0,
                long_entry_shares=0,
                short_shares=0,
                short_entry_price=0.0,
                short_entry_shares=0,
            )
        )

        for variable_name, values_by_candle_id in indicator_values.items():
            context[variable_name] = values_by_candle_id.get(candle_ts) or values_by_candle_id.get(candle_id)

        context["_in_bull_trade"] = False
        context["_in_bear_trade"] = False
        context_history.append(context.copy())
        context_history[-1]["_long_entry_taken"] = False
        context_history[-1]["_short_entry_taken"] = False

        try:
            if _is_boolean_expression_node(ast):
                numeric_value = 1.0 if _evaluate_expression(ast, context, False, False, context_history, None) else 0.0
            else:
                numeric_value = _evaluate_value(ast, context, False, False, context_history, None)
        except ValueError as exc:
            error_msg = str(exc)
            if "is not available or non-numeric" in error_msg:
                continue
            if "exceeds available history" in error_msg:
                continue
            raise

        base_record: dict[str, Any] = {
            "id": f"research-{normalized_schema}-{session_id}-{candle_id}",
            "ts": candle_ts,
            "metadata": {"subgraph": True},
        }

        if normalized_schema == "area":
            base_record["upper"] = max(float(numeric_value), 0.0)
            base_record["lower"] = min(float(numeric_value), 0.0)
        else:
            base_record["value"] = float(numeric_value)

        records.append(base_record)

    return {
        "session_id": session_id,
        "expression": normalized_expression,
        "schema": normalized_schema,
        "records": records,
        "count": len(records),
    }


def evaluate_strategy(
    session_id: str,
    strategy_name: str,
    long_entry_rules: list[str] | None = None,
    long_exit_rules: list[str] | None = None,
    data_store: SessionDataStore | None = None,
    short_entry_rules: list[str] | None = None,
    short_exit_rules: list[str] | None = None,
    # Backward compatibility: accept single-string format
    long_entry_rule: str = "",
    short_entry_rule: str = "",
) -> dict[str, Any]:
    # Support both new array format and old single-string format
    if long_entry_rule and not long_entry_rules:
        long_entry_rules = [long_entry_rule]
    if short_entry_rule and not short_entry_rules:
        short_entry_rules = [short_entry_rule]
        
    normalized_long_entries = [rule.strip() for rule in (long_entry_rules or []) if rule and rule.strip()]
    normalized_long_exits = [rule.strip() for rule in (long_exit_rules or []) if rule and rule.strip()]
    normalized_short_entries = [rule.strip() for rule in (short_entry_rules or []) if rule and rule.strip()]
    normalized_short_exits = [rule.strip() for rule in (short_exit_rules or []) if rule and rule.strip()]

    validation = validate_strategy_v2(
        long_entry_rules=normalized_long_entries,
        long_exit_rules=normalized_long_exits,
        short_entry_rules=normalized_short_entries,
        short_exit_rules=normalized_short_exits,
    )
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))

    long_entry_asts = [parse_rule(rule) for rule in normalized_long_entries]
    long_exit_asts = [parse_rule(rule) for rule in normalized_long_exits]
    short_entry_asts = [parse_rule(rule) for rule in normalized_short_entries]
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
    long_shares = 0
    long_entry_price = 0.0
    long_entry_shares = 0
    short_shares = 0
    short_entry_price = 0.0
    short_entry_shares = 0
    closed_trade_pnls: list[float] = []
    closed_trade_returns: list[float] = []
    equity_curve: list[dict[str, Any]] = []
    daily_pnl = 0.0
    current_day_et: Any = None

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
        candle_date_et = None
        try:
            dt_utc = datetime.fromisoformat(candle_ts.replace('Z', '+00:00'))
            dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
            hour_et = dt_et.hour
            minute_et = dt_et.minute
            time_hhmm = hour_et * 100 + minute_et
            candle_date_et = dt_et.date()
        except (ValueError, KeyError, OSError):
            # If timestamp parsing fails, set to 0
            hour_et = 0
            minute_et = 0
            time_hhmm = 0

        # Daily P&L reset on new trading day (Eastern Time)
        if candle_date_et is not None:
            if current_day_et is None:
                current_day_et = candle_date_et
            elif candle_date_et != current_day_et:
                daily_pnl = 0.0
                current_day_et = candle_date_et
        
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
            "DAILY_PNL": daily_pnl,
        }
        context.update(
            _build_position_context(
                close_price=close_price,
                long_shares=long_shares,
                long_entry_price=long_entry_price,
                long_entry_shares=long_entry_shares,
                short_shares=short_shares,
                short_entry_price=short_entry_price,
                short_entry_shares=short_entry_shares,
            )
        )

        for variable_name, values_by_candle_id in indicator_values.items():
            context[variable_name] = values_by_candle_id.get(candle_ts) or values_by_candle_id.get(candle_id)

        # Store state in context for historical lookback
        context["_in_bull_trade"] = in_bull_trade
        context["_in_bear_trade"] = in_bear_trade

        # Add current context to history
        context_history.append(context.copy())

        # Track whether entries were actually executed this bar (back-filled after actions)
        long_entry_executed = False
        short_entry_executed = False

        try:
            long_entry_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history, "long")
                for ast in long_entry_asts
            )
            long_exit_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history, "long")
                for ast in long_exit_asts
            )
            short_entry_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history, "short")
                for ast in short_entry_asts
            )
            short_exit_match = any(
                _evaluate_expression(ast, context, in_bull_trade, in_bear_trade, context_history, "short")
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

        if long_exit_match and in_bull_trade:
            in_bull_trade = False
            logger.info(
                "[TradeEngine] LONG EXIT SIGNAL ts=%s shares=%d",
                ts, long_shares
            )
            if long_shares > 0 and long_entry_shares > 0:
                cash += long_shares * fill_price
                pnl = (fill_price - long_entry_price) * long_entry_shares
                invested = long_entry_price * long_entry_shares
                if invested > 0:
                    closed_trade_returns.append(pnl / invested)
                closed_trade_pnls.append(pnl)
                daily_pnl += pnl
                logger.info(
                    "[TradeEngine] LONG EXIT EXECUTED shares=%d exit_price=%.2f entry_price=%.2f pnl=%.2f new_cash=%.2f",
                    long_entry_shares, fill_price, long_entry_price, pnl, cash
                )
                long_shares = 0
                long_entry_price = 0.0
                long_entry_shares = 0
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

        if short_exit_match and in_bear_trade:
            in_bear_trade = False
            logger.info(
                "[TradeEngine] SHORT EXIT SIGNAL ts=%s shares=%d",
                ts, short_shares
            )
            if short_shares > 0 and short_entry_shares > 0:
                cash -= short_entry_shares * fill_price
                pnl = (short_entry_price - fill_price) * short_entry_shares
                invested = short_entry_price * short_entry_shares
                if invested > 0:
                    closed_trade_returns.append(pnl / invested)
                closed_trade_pnls.append(pnl)
                daily_pnl += pnl
                logger.info(
                    "[TradeEngine] SHORT EXIT EXECUTED shares=%d exit_price=%.2f entry_price=%.2f pnl=%.2f cash=%.2f",
                    short_entry_shares, fill_price, short_entry_price, pnl, cash
                )
                short_shares = 0
                short_entry_price = 0.0
                short_entry_shares = 0
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

        if long_entry_match and not in_bull_trade:
            in_bull_trade = True
            long_entry_executed = True
            share_cost = fill_price
            buying_power = _compute_equity(cash, long_shares, short_shares, fill_price)
            quantity = int(buying_power // share_cost) if share_cost > 0 else 0

            logger.info(
                "[TradeEngine] LONG ENTRY SIGNAL ts=%s close=%.2f cash=%.2f quantity=%d",
                ts, fill_price, cash, quantity
            )
            
            if quantity > 0:
                cash -= quantity * fill_price
                long_shares = quantity
                long_entry_price = fill_price
                long_entry_shares = quantity
                logger.info(
                    "[TradeEngine] LONG ENTRY EXECUTED shares=%d entry_price=%.2f cost=%.2f remaining_cash=%.2f",
                    long_entry_shares, long_entry_price, long_entry_shares * long_entry_price, cash
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
        if short_entry_match and not in_bear_trade:
            in_bear_trade = True
            short_entry_executed = True
            share_cost = fill_price
            buying_power = _compute_equity(cash, long_shares, short_shares, fill_price)
            quantity = int(buying_power // share_cost) if share_cost > 0 else 0

            logger.info(
                "[TradeEngine] SHORT ENTRY SIGNAL ts=%s close=%.2f cash=%.2f quantity=%d",
                ts, fill_price, cash, quantity
            )

            if quantity > 0:
                cash += quantity * fill_price
                short_shares = quantity
                short_entry_price = fill_price
                short_entry_shares = quantity
                logger.info(
                    "[TradeEngine] SHORT ENTRY EXECUTED shares=%d entry_price=%.2f proceeds=%.2f cash=%.2f",
                    short_entry_shares, short_entry_price, short_entry_shares * short_entry_price, cash
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
        equity_curve.append({
            "ts": ts,
            "equity": _compute_equity(cash, long_shares, short_shares, fill_price),
        })

        # Back-fill entry execution flags into this bar's history snapshot
        context_history[-1]["_long_entry_taken"] = long_entry_executed
        context_history[-1]["_short_entry_taken"] = short_entry_executed

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
        "long_entry_rules": normalized_long_entries,
        "long_exit_rules": normalized_long_exits,
        "short_entry_rules": normalized_short_entries,
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
