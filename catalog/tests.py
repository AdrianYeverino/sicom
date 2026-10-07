from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from .models import CostSource, Product, ProductAlias, ProductSupplierCode, Supplier


class ProductConstraintsTest(TestCase):
    def test_a_cost_needs_a_known_source(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(name="Wood screw", last_cost=Decimal("1.50"), cost_source=CostSource.UNKNOWN)

    def test_a_known_source_needs_a_cost(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(name="Wood screw", cost_source=CostSource.SUPPLIER)

    def test_no_cost_is_unknown(self):
        product = Product.objects.create(name="Wood screw")
        self.assertEqual(product.cost_source, CostSource.UNKNOWN)


class SupplierCodeTest(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Acme Tools")
        self.product = Product.objects.create(name="Garden hose 1/2")

    def test_a_code_is_unique_per_supplier(self):
        ProductSupplierCode.objects.create(product=self.product, supplier=self.supplier, code="AT-1001")
        other = Product.objects.create(name="Garden hose 3/4")
        with self.assertRaises(IntegrityError):
            ProductSupplierCode.objects.create(product=other, supplier=self.supplier, code="AT-1001")

    def test_the_same_code_can_exist_at_another_supplier(self):
        ProductSupplierCode.objects.create(product=self.product, supplier=self.supplier, code="X-200")
        other = Supplier.objects.create(name="Northwind Hardware")
        ProductSupplierCode.objects.create(product=self.product, supplier=other, code="X-200")

    def test_supplier_names_ignore_case(self):
        with self.assertRaises(IntegrityError):
            Supplier.objects.create(name="ACME TOOLS")


class AliasTest(TestCase):
    def test_an_alias_is_unique_per_product_ignoring_case(self):
        product = Product.objects.create(name="Grey cement")
        ProductAlias.objects.create(product=product, alias="Portland")
        with self.assertRaises(IntegrityError):
            ProductAlias.objects.create(product=product, alias="PORTLAND")
