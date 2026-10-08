"""Sheets as they really come: line discounts, unit costs rounded to cents,
pages without their printed number. Invented numbers with the same shape."""

from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from catalog.models import Product
from receiving import reader, services
from receiving.models import LineReason
from receiving.verification import adds_up, merge, net_cost, proposed_quantity

from . import factories as f
from .test_services import content, ok, photo, read_line
from .test_verification import THRESHOLDS, line, page

D = Decimal


class ArithmeticTest(SimpleTestCase):
    def test_a_line_discount_is_part_of_the_amount(self):
        # 2 × 50.00 − 10 % = 90.00
        self.assertTrue(adds_up(D("2"), D("50.00"), D("10"), D("90.00"), D("0.01")))
        self.assertFalse(adds_up(D("2"), D("50.00"), None, D("90.00"), D("0.01")))

    def test_a_unit_cost_rounded_to_cents_drifts_with_the_quantity(self):
        # Printed 3.28 for a real 3.2845: 20 units print 65.69, not 65.60.
        self.assertTrue(adds_up(D("20"), D("3.28"), None, D("65.69"), D("0.01")))
        self.assertFalse(adds_up(D("20"), D("3.28"), None, D("66.00"), D("0.01")))

    def test_the_net_cost(self):
        self.assertEqual(net_cost(D("50.00"), D("10")), D("45.0000"))
        self.assertEqual(net_cost(D("50.00"), None), D("50.00"))

    def test_a_hidden_quantity_is_proposed_from_the_net_cost(self):
        hidden = line(quantity=None, cost="50.00", amount="90.00")
        hidden.discount_percent = D("10")
        self.assertEqual(proposed_quantity(hidden), D("2.000"))

    def test_a_page_without_its_printed_number_is_not_missing(self):
        merged = merge([("a", page(1, 2, [line()], folio="R-1")), ("b", page(None, None, [line()]))])
        self.assertEqual(merged.missing_pages, [])

    def test_a_really_missing_page_is_still_reported(self):
        merged = merge([("a", page(1, 3, [line()], folio="R-1")), ("b", page(None, None, [line()]))])
        self.assertEqual(merged.missing_pages, [3])


class DiscountFlowTest(TestCase):
    def test_the_entry_and_the_catalog_keep_the_net_cost(self):
        user = f.user("ana")
        discounted = read_line("PA-001", "WHITE PAINT 1L", quantity="2", cost="50.00", amount="90.00")
        discounted.discount_percent = D("10")
        document = services.start_document(user)
        with mock.patch.object(reader, "read_page", return_value=ok(content(1, 1, [discounted]))):
            services.add_page(document, user, photo("red"))
        document = services.finish_reading(document, user)
        paint = document.lines.get()
        self.assertEqual(paint.reason, LineReason.UNRECOGNIZED_PRODUCT)  # it adds up: only the product is unknown
        self.assertEqual(paint.net_unit_cost, D("45.00"))

        services.set_supplier(document, user)
        services.create_product_for(paint, user, "White paint 1 L")
        services.set_price(paint, user, margin_percent=D("50"))
        paint.refresh_from_db()
        self.assertEqual(paint.retail_price, D("78.30"))  # 45.00 × 1.16 × 1.5
        entry = services.confirm(document, user)

        self.assertEqual(entry.lines.get().unit_cost, D("45.00"))
        self.assertEqual(Product.objects.get(name="White paint 1 L").last_cost, D("45.00"))
