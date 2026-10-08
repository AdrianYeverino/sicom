from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from receiving import costs, reader, services
from receiving.models import PageReading, ReadingOutcome

from . import factories as f
from .test_services import content, ok, photo, read_line

D = Decimal


def reading(page, outcome=ReadingOutcome.OK, cost="0.0030", tokens=(4700, 1200), cached=False):
    return PageReading.objects.create(
        page=page, outcome=outcome, model="m", prompt_version="v3", image_side=2000,
        input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=D(cost) if cost else None, from_cache=cached,
    )


@override_settings(USD_MXN_RATE=D("20.00"))
class CostTest(TestCase):
    def setUp(self):
        self.doc = f.document()
        self.page = f.page(self.doc)

    def test_every_attempt_counts_and_converts_to_pesos(self):
        reading(self.page, outcome=ReadingOutcome.TIMEOUT, cost="0.0010", tokens=(4700, 0))
        reading(self.page)
        cost = costs.of_document(self.doc)
        self.assertEqual((cost.calls, cost.failed), (2, 1))
        self.assertEqual((cost.input_tokens, cost.output_tokens), (9400, 1200))
        self.assertEqual(cost.usd, D("0.0040"))
        self.assertEqual(cost.mxn, D("0.0800"))

    def test_a_replayed_reading_shows_its_tokens_but_costs_nothing(self):
        reading(self.page, cached=True)
        cost = costs.of_document(self.doc)
        self.assertEqual((cost.replayed, cost.input_tokens, cost.usd), (1, 4700, D("0")))

    def test_costs_per_document_in_one_query(self):
        other = f.document()
        reading(self.page)
        reading(f.page(other), cost="0.0050")
        with self.assertNumQueries(1):
            found = costs.by_document([self.doc.pk, other.pk])
        self.assertEqual(found[other.pk].usd, D("0.0050"))


@override_settings(USD_MXN_RATE=D("20.00"))
class CostScreensTest(TestCase):
    def test_metrics_entries_and_entry_show_tokens_dollars_and_pesos(self):
        user = f.user("boss")
        user.is_staff = True
        user.save()
        self.client.force_login(user)
        document = services.start_document(user)
        page = content(1, 1, [read_line("AT-1001", "GARDEN HOSE 1/2")])
        with mock.patch.object(reader, "read_page", return_value=ok(page)):  # 1200 + 900 tokens, 0.0015 USD
            services.add_page(document, user, photo("red"))
        document = services.finish_reading(document, user)
        services.set_supplier(document, user)
        services.create_product_for(document.lines.get(), user, "Garden hose")
        entry = services.confirm(document, user)

        self.assertContains(self.client.get(reverse("receiving:metrics")), "$0.03 MXN")
        listing = self.client.get(reverse("receiving:entries"))
        self.assertContains(listing, "$0.03")
        self.assertContains(listing, "0.0015 USD")
        detail = self.client.get(reverse("receiving:entry", args=[entry.pk]))
        self.assertContains(detail, "1,200 + 900")
        self.assertContains(self.client.get(reverse("receiving:review", args=[document.pk])), "$0.03 MXN")
