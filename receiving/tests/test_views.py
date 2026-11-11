"""Every screen renders, and the review actions answer with the fresh card."""

import io
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from receiving import reader, services
from receiving.models import DocumentStatus, LineStatus

from . import factories as f
from .test_services import content, failed, ok, read_line

D = Decimal


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(buffer, format="JPEG")
    buffer.name = "IMG_0001.jpeg"
    buffer.seek(0)
    return buffer


class ScreensTest(TestCase):
    def setUp(self):
        self.user = f.user("ana")
        self.client.force_login(self.user)

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(reverse("receiving:documents"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_an_empty_list_teaches_what_to_do(self):
        response = self.client.get(reverse("receiving:documents"))
        self.assertContains(response, "Recibir mercancía")

    def test_upload_read_review_and_confirm(self):
        response = self.client.post(reverse("receiving:document_new"))
        document_url = response["Location"]
        document = services.ReceivedDocument.objects.get()
        self.assertEqual(self.client.get(document_url).status_code, 200)

        page = content(1, 1, [read_line("AT-1001", "GARDEN HOSE 1/2")], totals=(D("81.00"), D("12.96"), D("93.96")))
        with mock.patch.object(reader, "read_page", return_value=ok(page)):
            response = self.client.post(reverse("receiving:page_add", args=[document.pk]), {"photo": photo()})
        self.assertContains(response, "Leída")

        page_obj = document.pages.get()
        thumb = self.client.get(reverse("receiving:thumbnail", args=[page_obj.pk]))
        self.assertEqual(thumb["Content-Type"], "image/webp")

        self.assertRedirects(self.client.post(reverse("receiving:finish", args=[document.pk])),
                             reverse("receiving:review", args=[document.pk]))
        review = self.client.get(reverse("receiving:review", args=[document.pk]))
        self.assertContains(review, "GARDEN HOSE 1/2")
        self.assertContains(review, "Producto no reconocido")

        line = document.lines.get()
        self.client.post(reverse("receiving:supplier", args=[document.pk]), {"name": "ACME TOOLS", "rfc": "ACM010101AB1"})
        card = self.client.post(reverse("receiving:line_new_product", args=[line.pk]), {"name": "Garden hose 1/2"})
        self.assertEqual(card["HX-Trigger"], "lineChanged")
        card = self.client.post(reverse("receiving:line_price", args=[line.pk]), {"by": "margin", "margin_percent": "50"})
        self.assertContains(card, "70.47")
        summary = self.client.get(reverse("receiving:summary", args=[document.pk]))
        self.assertNotContains(summary, "disabled")

        response = self.client.post(reverse("receiving:confirm", args=[document.pk]))
        entry = document.entry
        self.assertRedirects(response, reverse("receiving:entry", args=[entry.pk]))
        self.assertContains(self.client.get(reverse("receiving:entry", args=[entry.pk])), "Garden hose 1/2")
        self.assertContains(self.client.get(reverse("receiving:entries")), "ACME TOOLS")

        csv = self.client.get(reverse("receiving:entries") + "?format=csv")
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        body = csv.content.decode("utf-8-sig")
        self.assertIn("Garden hose 1/2", body)
        self.assertIn("70.47", body)

    @override_settings(READER_MODEL="minimax/minimax-m3", READER_BACKUP_MODEL="qwen/qwen3.5-flash-02-23")
    def test_a_page_read_by_the_backup_says_so_and_why(self):
        document = services.start_document(self.user)
        page = content(1, 1, [read_line("AT-1001", "GARDEN HOSE 1/2")])

        def read_page(image, model=None):
            result = failed(reader.NO_ENDPOINT) if model == "minimax/minimax-m3" else ok(page)
            result.usage.model = model
            return result

        with mock.patch.object(reader, "read_page", side_effect=read_page):
            response = self.client.post(reverse("receiving:page_add", args=[document.pk]), {"photo": photo()})
        self.assertContains(response, "Leída con el modelo de reserva (Qwen 3.5 Flash)")
        self.assertContains(response, "MiniMax M3 no pudo leerla: ningún proveedor disponible")

        self.client.post(reverse("receiving:finish", args=[document.pk]))
        review = self.client.get(reverse("receiving:review", args=[document.pk]))
        self.assertContains(review, "se leyó con el modelo de reserva (Qwen 3.5 Flash)")

    @override_settings(READER_BACKUP_MODEL="")
    def test_a_failed_page_says_what_to_do(self):
        document = services.start_document(self.user)
        with mock.patch.object(reader, "read_page", return_value=failed(reader.NO_CREDIT)):
            response = self.client.post(reverse("receiving:page_add", args=[document.pk]), {"photo": photo()})
        self.assertContains(response, "Sin crédito")
        self.assertContains(response, "Avisa al administrador para que lo recargue")
        self.assertContains(response, "Detalle técnico")

    def test_a_photo_that_cannot_be_opened_answers_with_the_reason(self):
        document = services.start_document(self.user)
        bad = io.BytesIO(b"not an image")
        bad.name = "notes.pdf"
        response = self.client.post(reverse("receiving:page_add", args=[document.pk]), {"photo": bad})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "no es una imagen", status_code=400)

    def test_an_error_shows_on_the_card_without_breaking_the_page(self):
        document = f.document(self.user, status=DocumentStatus.IN_REVIEW, thresholds={"amount_tolerance": "0.01"})
        line = f.line(document)
        response = self.client.post(reverse("receiving:line_accept", args=[line.pk]))
        self.assertContains(response, "Liga el renglón a un producto")

    def test_not_arrived_and_undo(self):
        document = f.document(self.user, status=DocumentStatus.IN_REVIEW, thresholds={"amount_tolerance": "0.01"})
        line = f.line(document)
        self.assertContains(self.client.post(reverse("receiving:line_not_arrived", args=[line.pk])), "Deshacer")
        line.refresh_from_db()
        self.assertEqual(line.status, LineStatus.DISCARDED)
        self.client.post(reverse("receiving:line_undo", args=[line.pk]))
        line.refresh_from_db()
        self.assertNotEqual(line.status, LineStatus.DISCARDED)
