"""Pure, deterministic Document Naming Rule resolution for J19B2H."""

from copy import deepcopy


SUPPORTED_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "in", "not in"}


def resolve_document_naming_rule(rules, document_type, facts):
    """Resolve one enabled highest-priority rule or fail closed."""
    matches = []
    for source in rules or []:
        rule = deepcopy(source)
        if rule.get("document_type") != document_type or _truthy(rule.get("disabled")):
            continue
        if all(_condition_matches(condition, facts) for condition in rule.get("conditions") or []):
            matches.append(rule)
    if not matches:
        raise ValueError("NO_APPLICABLE_DOCUMENT_NAMING_RULE")
    highest = max(int(rule.get("priority") or 0) for rule in matches)
    winners = [rule for rule in matches if int(rule.get("priority") or 0) == highest]
    if len(winners) != 1:
        raise ValueError("AMBIGUOUS_DOCUMENT_NAMING_RULE")
    rule = winners[0]
    if not rule.get("name") or not rule.get("prefix") or int(rule.get("prefix_digits") or 0) <= 0:
        raise ValueError("DOCUMENT_NAMING_RULE_CONFIGURATION_INVALID")
    counter = int(rule.get("counter") or 0)
    if counter < 0:
        raise ValueError("DOCUMENT_NAMING_RULE_COUNTER_INVALID")
    return rule


def naming_rule_snapshot(rule):
    """Return the canonical expected-current reservation snapshot."""
    conditions = []
    for row in sorted(rule.get("conditions") or [], key=lambda item: int(item.get("idx") or 0)):
        conditions.append({
            "field": row.get("field"), "condition": row.get("condition"),
            "value": row.get("value"), "idx": int(row.get("idx") or 0),
        })
    return {
        "name": rule.get("name"), "modified": str(rule.get("modified") or ""),
        "document_type": rule.get("document_type"),
        "disabled": int(rule.get("disabled") or 0),
        "priority": int(rule.get("priority") or 0), "prefix": rule.get("prefix"),
        "prefix_digits": int(rule.get("prefix_digits") or 0),
        "counter": int(rule.get("counter") or 0), "conditions": conditions,
    }


def forecast_from_naming_rule(rule):
    counter = int(rule.get("counter") or 0)
    digits = int(rule.get("prefix_digits") or 0)
    return str(rule.get("prefix")) + str(counter + 1).zfill(digits)


def _condition_matches(condition, facts):
    field = condition.get("field")
    operator = (condition.get("condition") or "=").strip().lower()
    if not field or operator not in SUPPORTED_OPERATORS:
        raise ValueError("DOCUMENT_NAMING_RULE_CONDITION_UNSUPPORTED")
    actual = facts.get(field)
    expected = condition.get("value")
    if operator in ("in", "not in"):
        values = [value.strip() for value in str(expected or "").split(",")]
        result = str(actual) in values
        return result if operator == "in" else not result
    left, right = _comparable(actual), _comparable(expected)
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    try:
        if operator == ">": return left > right
        if operator == ">=": return left >= right
        if operator == "<": return left < right
        if operator == "<=": return left <= right
    except TypeError:
        return False
    return False


def _comparable(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
