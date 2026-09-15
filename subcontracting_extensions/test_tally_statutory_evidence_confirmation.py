"""J19B2L controlled-service source contract tests."""

from pathlib import Path
import unittest


class TestTallyStatutoryEvidenceConfirmation(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).with_name(
            "tally_statutory_evidence_confirmation.py"
        ).read_text()

    def test_contract_is_exact_and_non_executing(self):
        self.assertIn('CONTRACT_VERSION = "J19B2L"', self.source)
        self.assertIn('OUTCOME = "NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"', self.source)
        for marker in (
            '"submission_authorized": False',
            '"stock_posting_authorized": False',
            '"accounting_posting_authorized": False',
            '"statutory_generation_authorized": False',
            '"tax_posting_authorized": False',
            '"lot_closure_authorized": False',
        ):
            self.assertIn(marker, self.source)
        self.assertNotIn(".submit(", self.source)

    def test_requires_permission_attestation_reason_and_stale_checks(self):
        for marker in (
            "_require_system_manager(api)", "confirmation_attested",
            "validate_reason(reason)", "_same_modified(invoice",
            "expected_statutory_evidence", "EXPECTED_BLOCKERS",
            'api.db.count("Stock Ledger Entry"', 'api.db.count("GL Entry"',
        ):
            self.assertIn(marker, self.source)


if __name__ == "__main__":
    unittest.main()
