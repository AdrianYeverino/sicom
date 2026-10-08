from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from catalog.models import Product
from receiving import pricing

D = Decimal


@override_settings(TAX_RATE=D("0.16"), DEFAULT_MARGIN_PERCENT=D("55"))
class PricingTest(SimpleTestCase):
    def test_price_includes_tax_and_margin(self):
        # 40.00 × 1.16 = 46.40; with 50 % = 69.60
        self.assertEqual(pricing.price_for(D("40.00"), D("50")), D("69.60"))

    def test_the_margin_a_price_implies(self):
        self.assertEqual(pricing.margin_for(D("40.00"), D("69.60")), D("50.00"))

    def test_no_cost_no_margin(self):
        self.assertIsNone(pricing.margin_for(D("0"), D("10")))

    def test_a_new_product_gets_the_store_default(self):
        (option,) = pricing.options(D("40.00"))
        self.assertEqual((option.key, option.margin_percent), ("default", D("55")))

    def test_a_handwritten_price_comes_first(self):
        first = pricing.options(D("40.00"), handwritten_price=D("70.00"))[0]
        self.assertEqual((first.key, first.retail_price), ("handwritten", D("70.00")))

    def test_a_new_cost_offers_keeping_the_margin_or_the_price(self):
        product = Product(name="Garden hose", margin_percent=D("50"), retail_price=D("69.60"))
        found = {o.key: o for o in pricing.options(D("44.00"), product)}
        self.assertEqual(found["keep_margin"].retail_price, D("76.56"))  # 44 × 1.16 × 1.5
        self.assertEqual(found["keep_price"].retail_price, D("69.60"))
        self.assertEqual(found["keep_price"].margin_percent, D("36.36"))
        self.assertNotIn("default", found)
