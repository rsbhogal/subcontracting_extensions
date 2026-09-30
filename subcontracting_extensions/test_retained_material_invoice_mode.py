"""Mode pinning cannot reinterpret an existing controlled Draft."""

import unittest

from subcontracting_extensions.retained_material_invoice_mode import (
    ERPNEXT_PRIMARY, TALLY, creation_mode, event_mode, invoice_mode,
    erpnext_einvoice_request, verify_draft_allocation,
)


class TestInvoiceMode(unittest.TestCase):
    def test_creation_is_explicit(self):
        self.assertEqual(creation_mode({"sales_invoice_number_coordination_mode": ERPNEXT_PRIMARY}), ERPNEXT_PRIMARY)
        with self.assertRaises(ValueError):
            creation_mode({"sales_invoice_number_coordination_mode": "DISABLED"})

    def test_historical_tally_event_remains_tally(self):
        event = {"reservation": "R", "tally_confirmation": "C"}
        invoice = {"custom_invoice_number_reservation": "R", "custom_tally_reservation_confirmation": "C"}
        self.assertEqual(event_mode(event), TALLY)
        self.assertEqual(invoice_mode(invoice, event), TALLY)

    def test_erpnext_primary_requires_explicit_marker_and_empty_tally_links(self):
        event = {"coordination_mode": ERPNEXT_PRIMARY}
        invoice = {"custom_retained_material_invoice_mode": ERPNEXT_PRIMARY}
        self.assertEqual(invoice_mode(invoice, event), ERPNEXT_PRIMARY)
        with self.assertRaises(ValueError):
            invoice_mode({}, event)
        with self.assertRaises(ValueError):
            invoice_mode({**invoice, "custom_invoice_number_reservation": "R"}, event)

    def test_mixed_mode_evidence_rejected(self):
        with self.assertRaises(ValueError):
            event_mode({"coordination_mode": ERPNEXT_PRIMARY, "reservation": "R"})
        with self.assertRaises(ValueError):
            event_mode({"coordination_mode": TALLY})

    def test_inserted_draft_must_consume_exactly_one_reviewed_rule_number(self):
        verify_draft_allocation("U-I/26-27/0018", "U-I/26-27/0018", 17, 18, 0)
        for args in (("U-I/26-27/0018", "U-I/26-27/0019", 17, 18, 0),
                     ("U-I/26-27/0018", "U-I/26-27/0018", 17, 19, 0),
                     ("U-I/26-27/0018", "U-I/26-27/0018", 17, 18, 1)):
            with self.assertRaises(ValueError):
                verify_draft_allocation(*args)

    def test_erpnext_einvoice_is_requested_only_if_applicable_and_ready(self):
        self.assertFalse(erpnext_einvoice_request(False, False, False))
        self.assertTrue(erpnext_einvoice_request(True, True, True))
        for api_enabled, auto_generate in ((False, True), (True, False)):
            with self.assertRaises(ValueError):
                erpnext_einvoice_request(True, api_enabled, auto_generate)
        with self.assertRaisesRegex(ValueError, "e-Waybill"):
            erpnext_einvoice_request(True, True, True, True)


if __name__ == "__main__":
    unittest.main()
