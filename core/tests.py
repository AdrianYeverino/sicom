from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import CostSource, Product

from .history import Source, changes_by
from .models import ChangeLog


class HistoryTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ana", password="x-test-pass-123")

    def logs(self, product):
        return ChangeLog.objects.filter(table_name="products", row_id=product.pk).order_by("changed_at", "id")

    def test_a_change_records_only_what_changed_with_old_and_new(self):
        with changes_by(self.user, Source.REVIEW):
            product = Product.objects.create(name="Garden hose 1/2")
        product = Product.objects.get(pk=product.pk)
        with changes_by(self.user, Source.REVIEW):
            product.margin_percent = Decimal("55")
            product.retail_price = Decimal("95.00")
            product.save()

        create, update = self.logs(product)
        self.assertEqual(create.action, "create")
        self.assertEqual(update.action, "update")
        self.assertEqual(update.changes, {"margin_percent": [None, "55"], "retail_price": [None, "95.00"]})
        self.assertEqual(update.changed_by, self.user)
        self.assertEqual(update.source, Source.REVIEW)

    def test_saving_without_changes_writes_nothing(self):
        product = Product.objects.create(name="Wood screw")
        product = Product.objects.get(pk=product.pk)
        product.save()
        self.assertEqual(self.logs(product).filter(action="update").count(), 0)

    def test_what_the_reader_writes_is_not_logged(self):
        with changes_by(None, Source.READER):
            product = Product.objects.create(name="Wood screw", last_cost=Decimal("1.50"), cost_source=CostSource.SUPPLIER)
        self.assertFalse(self.logs(product).exists())

    def test_updated_at_moves_on_every_save(self):
        product = Product.objects.create(name="Wood screw")
        first = product.updated_at
        product.name = "Wood screw 1in"
        product.save()
        self.assertGreater(product.updated_at, first)
