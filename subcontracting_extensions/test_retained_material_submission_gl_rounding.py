"""Native GL currency/round-off expectations; no site fixtures or writes."""
import unittest
from unittest.mock import patch
from subcontracting_extensions import retained_material_sales_invoice_submission as service

class TestControlledSubmissionGLRounding(unittest.TestCase):
    def facts(self, net=100, tax=18, rounded=None):
        grand = round(net + tax, 2)
        adjustment = round(rounded - grand, 2) if rounded is not None else 0
        invoice = dict(name='TEST', company='COMPANY', debit_to='AR', net_total=net,
            base_net_total=net, grand_total=grand, base_grand_total=grand,
            rounded_total=rounded or 0, base_rounded_total=rounded or 0,
            rounding_adjustment=adjustment, base_rounding_adjustment=adjustment,
            taxes=[dict(account_head='TAX', tax_amount=tax, base_tax_amount=tax)])
        item = dict(qty=1, stock_qty=1, warehouse='WAREHOUSE', income_account='INCOME', expense_account='COGS')
        sle = [dict(actual_qty=-1, qty_after_transaction=0, stock_value_difference=-10)]
        receivable = rounded if adjustment else grand
        gl = [dict(account='AR', debit=receivable, credit=0),
              dict(account='INCOME', debit=0, credit=net),
              dict(account='TAX', debit=0, credit=tax),
              dict(account='COGS', debit=10, credit=0),
              dict(account='STOCK', debit=0, credit=10)]
        if adjustment:
            gl.append(dict(account='ROUND', debit=max(-adjustment, 0), credit=max(adjustment, 0)))
        return invoice, item, sle, gl

    def validate(self, facts):
        with patch.object(service, '_warehouse_account', return_value='STOCK'), \
             patch.object(service, '_round_off_account', return_value='ROUND'):
            service._validate_postings(None, *facts, 10)

    def test_unrounded_invoice_remains_supported(self):
        self.validate(self.facts())

    def test_rounding_down_posts_round_off_debit(self):
        self.validate(self.facts(net=100.05, rounded=118))

    def test_rounding_up_posts_round_off_credit(self):
        self.validate(self.facts(net=99.95, rounded=118))

    def test_missing_round_off_entry_is_rejected(self):
        facts = self.facts(net=100.05, rounded=118)
        facts[3].pop()
        with self.assertRaisesRegex(ValueError, 'GL entries do not match'):
            self.validate(facts)

    def test_wrong_round_off_account_is_rejected(self):
        facts = self.facts(net=100.05, rounded=118)
        facts[3][-1]['account'] = 'WRONG'
        with self.assertRaisesRegex(ValueError, 'GL entries do not match'):
            self.validate(facts)

    def test_wrong_tax_amount_is_rejected(self):
        facts = self.facts(net=100.05, rounded=118)
        facts[3][2]['credit'] += 1
        with self.assertRaisesRegex(ValueError, 'GL entries do not match'):
            self.validate(facts)

    def test_gl_expectation_uses_base_currency_amounts(self):
        facts = self.facts()
        invoice = facts[0]
        invoice.update(net_total=200, grand_total=236)
        invoice['taxes'][0]['tax_amount'] = 36
        self.validate(facts)

if __name__ == '__main__':
    unittest.main()
