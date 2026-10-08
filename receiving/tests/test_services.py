"""The whole flow with the model replaced by invented pages."""

import io
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from PIL import Image

from catalog.models import CostSource, Product, ProductSupplierCode, Supplier
from core.models import ChangeLog
from receiving import reader, services
from receiving.models import (
    DocumentStatus,
    DocumentWarning,
    LineReason,
    LineStatus,
    PageReading,
    ReadStatus,
)

from . import factories as f

D = Decimal


def photo(color):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def read_line(code, description, quantity="2", cost="40.50", amount="81.00", confidence="1", written=None):
    return reader.ReadLine(
        quantity=None if quantity is None else D(quantity), unit="PZA", supplier_code=code,
        description=description, unit_cost=D(cost), amount=D(amount),
        handwritten_price=None if written is None else D(written), confidence=D(confidence),
    )


def content(number, count, lines, header=True, totals=None):
    subtotal, tax, total = totals or (None, None, None)
    return reader.PageContent(
        page_number=number, page_count=count,
        supplier_name="ACME TOOLS" if header else "", supplier_rfc="ACM010101AB1" if header else "",
        document_type="Factura" if header else "", folio="F-100" if header else "",
        date="2026-01-15" if header else None, subtotal=subtotal, tax=tax, total=total, lines=lines,
    )


def ok(page_content):
    raw = {
        "es_hoja": True, "pagina": page_content.page_number, "paginas_totales": page_content.page_count,
        "proveedor": page_content.supplier_name or None, "rfc_proveedor": page_content.supplier_rfc or None,
        "tipo_documento": page_content.document_type or None, "folio": page_content.folio or None,
        "fecha": page_content.date,
        "subtotal": None if page_content.subtotal is None else float(page_content.subtotal),
        "iva": None if page_content.tax is None else float(page_content.tax),
        "total": None if page_content.total is None else float(page_content.total),
        "renglones": [
            {"cantidad": None if l.quantity is None else float(l.quantity), "unidad": l.unit, "clave": l.supplier_code,
             "descripcion": l.description, "costo_unitario": float(l.unit_cost), "descuento": None if l.discount_percent is None else float(l.discount_percent), "importe": float(l.amount),
             "precio_escrito": None if l.handwritten_price is None else float(l.handwritten_price),
             "confianza": float(l.confidence)}
            for l in page_content.lines
        ],
    }
    usage = reader.Usage(model="m", provider="provider-x", input_tokens=1200, output_tokens=900, cost_usd=D("0.0015"), seconds=D("9"))
    return reader.PageResult(reader.OK, usage, content=reader.to_content(raw), raw=raw)


def failed(outcome):
    return reader.PageResult(outcome, reader.Usage(model="m"), error_detail="x")


class FlowTest(TestCase):
    def setUp(self):
        self.user = f.user("ana")
        self.page1 = content(1, 2, [
            read_line("AT-1001", "GARDEN HOSE 1/2"),
            read_line("AT-2002", "WOOD SCREW 1IN", quantity="100", cost="0.50", amount="50.00", written="1.50"),
        ])
        self.page2 = content(2, 2, [read_line("AT-3003", "PAINT BRUSH 2IN", quantity=None, cost="20.00", amount="60.00")],
                             header=False, totals=(D("191.00"), D("30.56"), D("221.56")))

    def upload(self, results):
        document = services.start_document(self.user)
        with mock.patch.object(reader, "read_page", side_effect=results):
            for i, _ in enumerate(results):
                services.add_page(document, self.user, photo(("red", "green", "blue")[i]), f"IMG_{i}.heic", "iPhone")
        return document

    def test_two_pages_become_one_document_in_review(self):
        document = self.upload([ok(self.page1), ok(self.page2)])
        document = services.finish_reading(document, self.user)
        self.assertEqual(document.status, DocumentStatus.IN_REVIEW)
        self.assertEqual((document.folio_read, document.total_read, document.printed_page_count), ("F-100", D("221.56"), 2))
        self.assertEqual(document.warnings, [])
        lines = list(document.lines.order_by("position"))
        self.assertEqual([l.position for l in lines], [1, 2, 3])
        self.assertEqual(lines[2].page.upload_order, 2)
        # A first delivery: nothing in the catalog yet.
        self.assertEqual({l.reason for l in lines[:2]}, {LineReason.UNRECOGNIZED_PRODUCT})
        self.assertEqual(lines[2].reason, LineReason.MISSING_VALUE)
        self.assertEqual(lines[1].handwritten_price, D("1.50"))
        # What the reader wrote is not in the change history.
        self.assertFalse(ChangeLog.objects.filter(table_name="provisional_lines").exists())

    def test_a_page_that_keeps_failing_is_marked_failed_with_every_attempt(self):
        pg = f.page(services.start_document(self.user))
        attempts = [failed(reader.RATE_LIMIT)] * 3
        with mock.patch.object(reader, "read_page", side_effect=attempts):
            services.read_with_retries(pg, b"jpeg", 2000, pause=lambda s: None)
        self.assertEqual(pg.read_status, ReadStatus.FAILED)
        self.assertEqual(PageReading.objects.filter(page=pg).count(), 3)

    def test_retries_stop_on_success(self):
        pg = f.page(services.start_document(self.user))
        with mock.patch.object(reader, "read_page", side_effect=[failed(reader.RATE_LIMIT), ok(self.page1)]):
            result = services.read_with_retries(pg, b"jpeg", 2000, pause=lambda s: None)
        self.assertTrue(result.ok)
        self.assertEqual(pg.read_status, ReadStatus.READ)
        self.assertEqual(PageReading.objects.filter(page=pg).count(), 2)

    def test_unrecoverable_failures_are_not_retried(self):
        pg = f.page(services.start_document(self.user))
        with mock.patch.object(reader, "read_page", side_effect=[failed(reader.AUTH)]) as read:
            services.read_with_retries(pg, b"jpeg", 2000, pause=lambda s: None)
        self.assertEqual(read.call_count, 1)

    def test_cannot_finish_with_an_unread_page(self):
        document = self.upload([ok(self.page1), failed(reader.SCHEMA_ERROR)])
        with self.assertRaises(services.ReceivingError):
            services.finish_reading(document, self.user)

    def review_and_confirm(self):
        document = services.finish_reading(self.upload([ok(self.page1), ok(self.page2)]), self.user)
        hose, screws, brush = document.lines.order_by("position")
        services.set_supplier(document, self.user)  # created from what was read
        services.create_product_for(hose, self.user, "Garden hose 1/2")
        services.create_product_for(screws, self.user, "Wood screw 1in")
        services.create_product_for(brush, self.user, "Paint brush 2in")
        services.correct_line(brush, self.user, quantity=D("3"))
        services.set_price(hose, self.user, margin_percent=D("50"))
        services.set_price(screws, self.user, retail_price=D("1.50"))
        services.mark_not_arrived(brush, self.user)
        services.set_received(screws, self.user, D("90"))
        return document, hose, screws, brush

    def test_confirming_records_the_entry_and_teaches_the_catalog(self):
        document, hose, screws, brush = self.review_and_confirm()
        self.assertEqual(services.blockers(document), [])
        entry = services.confirm(document, self.user)

        lines = {l.product.name: l for l in entry.lines.select_related("product")}
        self.assertEqual(set(lines), {"Garden hose 1/2", "Wood screw 1in"})  # the brush did not arrive
        self.assertEqual(lines["Wood screw 1in"].quantity, D("90"))
        self.assertEqual(lines["Garden hose 1/2"].retail_price, D("70.47"))  # 40.50 × 1.16 × 1.5

        hose_product = Product.objects.get(name="Garden hose 1/2")
        self.assertEqual((hose_product.last_cost, hose_product.cost_source), (D("40.50"), CostSource.SUPPLIER))
        self.assertEqual(hose_product.margin_percent, D("50.00"))
        supplier = Supplier.objects.get(rfc="ACM010101AB1")
        self.assertTrue(ProductSupplierCode.objects.filter(supplier=supplier, code="AT-1001", product=hose_product).exists())

        document.refresh_from_db()
        self.assertEqual(document.status, DocumentStatus.CONFIRMED)
        self.assertEqual(document.lines.filter(status=LineStatus.CONFIRMED).count(), 2)
        # Who set the margin, and when, is in the history.
        log = ChangeLog.objects.filter(table_name="products", row_id=hose_product.pk, source="confirm").get()
        self.assertEqual(log.changed_by, self.user)
        self.assertIn("margin_percent", log.changes)

    def test_the_next_sheet_of_the_supplier_is_recognized_by_code(self):
        document, *_ = self.review_and_confirm()
        services.confirm(document, self.user)
        again = services.finish_reading(self.upload([ok(self.page1)]), self.user)
        self.assertEqual(again.supplier.rfc, "ACM010101AB1")
        self.assertEqual(again.lines.filter(status=LineStatus.RESOLVED).count(), 2)
        self.assertIn(DocumentWarning.DUPLICATE_DOCUMENT, again.warnings)

    def test_a_misread_code_is_caught_by_its_description(self):
        document, *_ = self.review_and_confirm()
        services.confirm(document, self.user)
        misread = content(1, 1, [read_line("AT-1007", "GARDEN HOSE 1/2")])
        again = services.finish_reading(self.upload([ok(misread)]), self.user)
        self.assertEqual(again.lines.get().reason, LineReason.CODE_MISMATCH)

    def test_flagged_lines_block_confirming(self):
        document = services.finish_reading(self.upload([ok(self.page1)]), self.user)
        services.set_supplier(document, self.user)
        self.assertTrue(services.blockers(document))
        with self.assertRaises(services.ReceivingError):
            services.confirm(document, self.user)

    def test_a_confirmed_document_cannot_change(self):
        document, hose, *_ = self.review_and_confirm()
        services.confirm(document, self.user)
        hose.refresh_from_db()
        with self.assertRaises(services.ReceivingError):
            services.set_price(hose, self.user, margin_percent=D("10"))
        with self.assertRaises(services.ReceivingError):
            services.confirm(document, self.user)
