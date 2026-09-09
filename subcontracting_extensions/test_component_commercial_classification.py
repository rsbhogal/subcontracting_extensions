"""Focused J19B1D direction, revision and capability safety tests."""

import unittest

from subcontracting_extensions.component_commercial_classification import (
    _validate_expected_revision,
)


class Projection(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class TestCommercialDecisionConcurrency(unittest.TestCase):
    def projection(self, **changes):
        values = Projection(
            classification_revision=2,
            treatment_revision=1,
            last_decision_event="PLCD-00003",
        )
        values.update(changes)
        return values

    def test_current_projection_is_accepted(self):
        _validate_expected_revision(
            self.projection(), 2, 1, "PLCD-00003"
        )

    def test_stale_classification_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 1, 1, "PLCD-00003"
            )

    def test_stale_treatment_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 2, 0, "PLCD-00003"
            )

    def test_stale_last_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 2, 1, "PLCD-00002"
            )

    def test_new_scope_requires_explicit_zero_projection(self):
        _validate_expected_revision(Projection(), 0, 0, None)
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(Projection(), 1, 0, None)

    def test_non_integer_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be integers"):
            _validate_expected_revision(self.projection(), "bad", 1, "PLCD-00003")


if __name__ == "__main__":
    unittest.main()
