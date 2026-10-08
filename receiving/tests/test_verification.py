import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from receiving.matching import Recognition, normalize, similarity
from receiving.models import DocumentWarning, LineReason, LineStatus
from receiving.reader import PageContent, ReadLine
from receiving.verification import check_document, check_line, merge, proposed_quantity

D = Decimal
THRESHOLDS = {
    "amount_tolerance": D("0.01"),
    "subtotal_tolerance": D("0.05"),
    "min_confidence": D("0.80"),
    "match_similarity": D("0.85"),
    "suggest_similarity": D("0.60"),
    "cost_variation": D("0.15"),
}
KNOWN = Recognition(product=object(), how="code", score=1.0)


def line(quantity="2", cost="40.50", amount="81.00", confidence="1", code="AT-1001"):
    return ReadLine(
        quantity=None if quantity is None else D(quantity), unit="", supplier_code=code,
        description="GARDEN HOSE 1/2", unit_cost=D(cost), amount=D(amount),
        handwritten_price=None, confidence=D(confidence),
    )


def page(number=None, count=None, lines=(), folio="", subtotal=None, tax=None, total=None, date=None):
    return PageContent(
        page_number=number, page_count=count, supplier_name="ACME TOOLS" if folio else "",
        supplier_rfc="", document_type="Factura" if folio else "", folio=folio, date=date,
        subtotal=subtotal, tax=tax, total=total, lines=list(lines),
    )


class CheckLineTest(SimpleTestCase):
    def check(self, read, recognition=KNOWN, last_cost=None):
        return check_line(read, recognition, last_cost, THRESHOLDS)

    def test_a_clean_line_is_resolved(self):
        self.assertEqual(self.check(line()).status, LineStatus.RESOLVED)

    def test_a_hidden_quantity_comes_first(self):
        result = self.check(line(quantity=None, confidence="0.1"), Recognition())
        self.assertEqual((result.status, result.reason, result.amount_matches),
                         (LineStatus.FLAGGED, LineReason.MISSING_VALUE, None))

    def test_amounts_that_do_not_add_up(self):
        result = self.check(line(amount="80.00"))
        self.assertEqual((result.reason, result.amount_matches), (LineReason.AMOUNT_MISMATCH, False))

    def test_one_cent_of_tolerance(self):
        self.assertEqual(self.check(line(amount="81.01")).reason, LineReason.NONE)

    def test_low_confidence(self):
        self.assertEqual(self.check(line(confidence="0.7")).reason, LineReason.ILLEGIBLE_TEXT)

    def test_misread_code(self):
        self.assertEqual(self.check(line(), Recognition(code_mismatch=True)).reason, LineReason.CODE_MISMATCH)

    def test_unknown_product(self):
        self.assertEqual(self.check(line(), Recognition()).reason, LineReason.UNRECOGNIZED_PRODUCT)

    def test_a_cost_change_is_only_a_warning(self):
        result = self.check(line(), last_cost=D("30.00"))
        self.assertEqual((result.status, result.reason), (LineStatus.RESOLVED, LineReason.COST_VARIATION))

    def test_a_small_cost_change_says_nothing(self):
        self.assertEqual(self.check(line(), last_cost=D("39.00")).reason, LineReason.NONE)

    def test_the_hidden_quantity_is_proposed_from_the_amount(self):
        self.assertEqual(proposed_quantity(line(quantity=None, cost="70.14", amount="210.42")), D("3.000"))


class MergeTest(SimpleTestCase):
    def test_header_from_the_first_page_and_totals_from_the_last(self):
        first = page(1, 2, [line()], folio="R-0001", date="2026-01-15")
        second = page(2, 2, [line(code="AT-2002")], subtotal=D("162.00"), tax=D("25.92"), total=D("187.92"))
        merged = merge([("a", first), ("b", second)])
        self.assertEqual((merged.folio, merged.date), ("R-0001", datetime.date(2026, 1, 15)))
        self.assertEqual((merged.subtotal, merged.total), (D("162.00"), D("187.92")))
        self.assertEqual([key for key, _ in merged.lines], ["a", "b"])
        self.assertEqual(merged.missing_pages, [])

    def test_the_printed_page_number_wins_over_upload_order(self):
        merged = merge([("b", page(2, 2, [line(code="B")])), ("a", page(1, 2, [line(code="A")], folio="R-1"))])
        self.assertEqual([l.supplier_code for _, l in merged.lines], ["A", "B"])
        self.assertEqual(merged.folio, "R-1")

    def test_a_missing_page_is_detected(self):
        merged = merge([("a", page(1, 3, [line()], folio="R-1")), ("c", page(3, 3, [line()]))])
        self.assertEqual(merged.missing_pages, [2])


class CheckDocumentTest(SimpleTestCase):
    today = datetime.date(2026, 1, 20)

    def warnings(self, **header):
        merged = merge([("a", page(1, 1, [line(), line(amount="81.00")], folio="R-1", **header))])
        return check_document(merged, THRESHOLDS, D("0.16"), today=self.today)

    def test_a_sheet_that_adds_up(self):
        self.assertEqual(self.warnings(subtotal=D("162.00"), tax=D("25.92"), total=D("187.92"), date="2026-01-15"), [])

    def test_lines_that_do_not_add_up_to_the_subtotal(self):
        self.assertIn(DocumentWarning.SUBTOTAL_MISMATCH, self.warnings(subtotal=D("170.00")))

    def test_rounding_drift_grows_with_the_lines(self):
        many = [line(amount="81.00") for _ in range(21)]
        merged = merge([("a", page(1, 1, many, folio="R-1", subtotal=D("1700.48")))])  # 1701.00 printed lines
        self.assertEqual(check_document(merged, THRESHOLDS, D("0.16"), today=self.today), [])

    def test_subtotal_plus_tax_is_not_the_total(self):
        self.assertIn(DocumentWarning.TOTAL_MISMATCH, self.warnings(subtotal=D("162.00"), tax=D("25.92"), total=D("190.00")))

    def test_the_tax_is_not_16_percent(self):
        self.assertIn(DocumentWarning.TAX_MISMATCH, self.warnings(subtotal=D("162.00"), tax=D("10.00"), total=D("172.00")))

    def test_a_date_in_the_future(self):
        self.assertIn(DocumentWarning.UNUSUAL_DATE, self.warnings(date="2026-03-01"))


class SimilarityTest(SimpleTestCase):
    def test_normalizes_case_accents_and_punctuation(self):
        self.assertEqual(normalize("  Mang.  flexible  5/16  —  Válvula "), "MANG FLEXIBLE 5/16 VALVULA")

    def test_identical_after_normalizing(self):
        self.assertEqual(similarity("Mang. flexible 5/16", "MANG FLEXIBLE 5/16"), 1.0)

    def test_different_products_score_low(self):
        self.assertLess(similarity("GARDEN HOSE 1/2", "WOOD SCREW 1IN"), 0.5)
