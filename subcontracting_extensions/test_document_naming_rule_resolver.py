import unittest

from subcontracting_extensions.document_naming_rule_resolver import (
    forecast_from_naming_rule, naming_rule_snapshot, resolve_document_naming_rule,
)


def rule(name, prefix, priority, conditions, **changes):
    value = {"name": name, "document_type": "Sales Invoice", "disabled": 0,
             "priority": priority, "prefix": prefix, "prefix_digits": 4,
             "counter": 16, "modified": "2026-09-12", "conditions": conditions}
    value.update(changes)
    return value


class TestDocumentNamingRuleResolver(unittest.TestCase):
    rules = [
        rule("RETURN", "FY.-.CR-", 0,
             [{"field": "is_return", "condition": "=", "value": "1", "idx": 1}]),
        rule("DEBIT", "FY.-.DR-", 1,
             [{"field": "custom_is_debitservice", "condition": "=", "value": "1", "idx": 1}]),
        rule("OUTWARD", "U-I/26-27/", 0, [
            {"field": "is_return", "condition": "=", "value": "0", "idx": 1},
            {"field": "company", "condition": "=", "value": "Bhogals Private Limited", "idx": 2},
        ]),
    ]

    def test_resolves_outward_rule_dynamically(self):
        selected = resolve_document_naming_rule(
            self.rules, "Sales Invoice",
            {"company": "Bhogals Private Limited", "is_return": 0,
             "custom_is_debitservice": 0},
        )
        self.assertEqual(selected["name"], "OUTWARD")
        self.assertEqual(forecast_from_naming_rule(selected), "U-I/26-27/0017")

    def test_higher_priority_debit_rule_wins(self):
        selected = resolve_document_naming_rule(
            self.rules, "Sales Invoice",
            {"company": "Bhogals Private Limited", "is_return": 0,
             "custom_is_debitservice": 1},
        )
        self.assertEqual(selected["name"], "DEBIT")

    def test_site_specific_name_is_not_assumed(self):
        changed = [dict(row, name="o220hscg4s") if row["name"] == "OUTWARD" else row
                   for row in self.rules]
        selected = resolve_document_naming_rule(
            changed, "Sales Invoice",
            {"company": "Bhogals Private Limited", "is_return": False,
             "custom_is_debitservice": False},
        )
        self.assertEqual(selected["name"], "o220hscg4s")

    def test_equal_priority_overlap_fails_closed(self):
        duplicate = rule("OTHER", "OTHER-", 0, [])
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            resolve_document_naming_rule(
                self.rules + [duplicate], "Sales Invoice",
                {"company": "Bhogals Private Limited", "is_return": 0,
                 "custom_is_debitservice": 0},
            )

    def test_disabled_rule_is_ignored(self):
        rows = [dict(row, disabled=1) if row["name"] == "OUTWARD" else row
                for row in self.rules]
        with self.assertRaisesRegex(ValueError, "NO_APPLICABLE"):
            resolve_document_naming_rule(
                rows, "Sales Invoice", {"company": "Bhogals Private Limited",
                                         "is_return": 0, "custom_is_debitservice": 0}
            )

    def test_snapshot_is_canonical(self):
        snapshot = naming_rule_snapshot(self.rules[2])
        self.assertEqual(snapshot["counter"], 16)
        self.assertEqual([row["field"] for row in snapshot["conditions"]],
                         ["is_return", "company"])


if __name__ == "__main__":
    unittest.main()
