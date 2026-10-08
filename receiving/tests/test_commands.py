import io
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from receiving import reader, services
from receiving.models import DocumentStatus

from . import factories as f
from .test_services import content, ok, photo, read_line

D = Decimal


class MetricsTest(TestCase):
    def test_a_correction_counts_against_the_reader(self):
        user = f.user("ana")
        page = content(1, 1, [
            read_line("AT-1001", "GARDEN HOSE 1/2"),
            read_line("AT-2002", "WOOD SCREW 1IN", quantity="10", cost="0.50", amount="5.00"),
        ])
        document = services.start_document(user)
        with mock.patch.object(reader, "read_page", return_value=ok(page)):
            services.add_page(document, user, photo("red"))
        document = services.finish_reading(document, user)
        services.set_supplier(document, user)
        hose, screws = document.lines.order_by("position")
        services.create_product_for(hose, user, "Garden hose")
        services.create_product_for(screws, user, "Wood screw")
        services.correct_line(screws, user, quantity=D("12"), amount=D("6.00"))
        services.confirm(document, user)

        out = io.StringIO()
        call_command("metrics", stdout=out)
        text = out.getvalue()
        self.assertIn("renglones confirmados: 2", text)
        self.assertIn("Cantidad leída correcta:       50.0%", text)
        self.assertIn("Costo leído correcto:          100.0%", text)


class BenchTest(TestCase):
    def test_one_folder_per_document_and_copies_skipped(self):
        user = f.user("boss")
        user.is_superuser = True
        user.save()
        page = content(1, 1, [read_line("AT-1001", "GARDEN HOSE 1/2")])
        with TemporaryDirectory() as root:
            folder = Path(root, "Acme", "2026-01-15 Factura F-100")
            folder.mkdir(parents=True)
            (folder / "Pag 1.jpeg").write_bytes(photo("red"))
            (folder / "Copia pag 1.jpeg").write_bytes(photo("blue"))
            out = io.StringIO()
            with mock.patch.object(reader, "read_page", return_value=ok(page)):
                call_command("bench", root, skip="Copia", user="boss", stdout=out)
        document = services.ReceivedDocument.objects.get()
        self.assertEqual(document.pages.count(), 1)
        self.assertEqual(document.status, DocumentStatus.IN_REVIEW)
        self.assertIn("1 documentos", out.getvalue())
