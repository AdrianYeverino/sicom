from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from catalog.models import Product, Supplier
from receiving.models import ConfirmedEntry, EntryLine, LineReason, LineStatus, PageReading, ReadingOutcome

from . import factories as f


class ProvisionalLineConstraintsTest(TestCase):
    def setUp(self):
        self.doc = f.document()
        self.page = f.page(self.doc)

    def test_a_flagged_line_says_why(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, status=LineStatus.FLAGGED)

    def test_a_flagged_line_with_its_reason(self):
        f.line(self.doc, self.page, status=LineStatus.FLAGGED, reason=LineReason.CODE_MISMATCH)

    def test_a_resolved_line_may_carry_the_cost_warning(self):
        f.line(self.doc, self.page, reason=LineReason.COST_VARIATION)

    def test_a_resolved_line_cannot_carry_a_flag_reason(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, reason=LineReason.ILLEGIBLE_TEXT)

    def test_a_hidden_quantity_has_no_match_result(self):
        f.line(
            self.doc, self.page, quantity=None, amount_matches=None,
            status=LineStatus.FLAGGED, reason=LineReason.MISSING_VALUE,
        )

    def test_a_match_result_needs_a_quantity(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, quantity=None, amount_matches=True,
                   status=LineStatus.FLAGGED, reason=LineReason.MISSING_VALUE)

    def test_did_not_arrive_means_zero_received(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, status=LineStatus.DISCARDED, received_quantity=Decimal("1"))

    def test_did_not_arrive_with_zero(self):
        f.line(self.doc, self.page, status=LineStatus.DISCARDED, received_quantity=Decimal("0"))

    def test_confidence_is_between_0_and_1(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, confidence=Decimal("1.5"))

    def test_margins_are_not_negative(self):
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, margin_percent=Decimal("-5"))

    def test_one_line_per_position(self):
        f.line(self.doc, self.page, position=1)
        with self.assertRaises(IntegrityError):
            f.line(self.doc, self.page, position=1)


class DocumentPageConstraintsTest(TestCase):
    def test_one_page_per_order(self):
        doc = f.document()
        f.page(doc, order=1)
        with self.assertRaises(IntegrityError):
            f.page(doc, order=1)

    def test_the_fingerprint_is_a_sha256(self):
        with self.assertRaises(IntegrityError):
            f.page(f.document(), file_sha256="not-a-hash")


class PageReadingTest(TestCase):
    def test_every_attempt_is_kept(self):
        page = f.page(f.document())
        common = {"page": page, "model": "m", "prompt_version": "v2", "image_side": 2000}
        PageReading.objects.create(outcome=ReadingOutcome.TIMEOUT, **common)
        PageReading.objects.create(outcome=ReadingOutcome.OK, **common)
        self.assertEqual(page.readings.count(), 2)


class ConfirmedEntryTest(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Acme Tools")
        self.user = f.user()

    def entry(self, folio):
        return ConfirmedEntry.objects.create(
            document=f.document(self.user), supplier=self.supplier, folio=folio,
            entry_date="2026-01-15", confirmed_by=self.user,
        )

    def test_the_same_folio_cannot_be_confirmed_twice(self):
        self.entry("R-0001")
        with self.assertRaises(IntegrityError):
            self.entry("R-0001")

    def test_documents_without_folio_do_not_collide(self):
        self.entry("")
        self.entry("")

    def test_an_entry_line_needs_a_positive_quantity(self):
        entry = self.entry("R-0002")
        doc_line = f.line(entry.document)
        product = Product.objects.create(name="Garden hose 1/2")
        with self.assertRaises(IntegrityError):
            EntryLine.objects.create(entry=entry, source_line=doc_line, product=product,
                                     quantity=Decimal("0"), unit_cost=Decimal("40.50"))
