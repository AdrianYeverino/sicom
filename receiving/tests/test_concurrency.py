"""Two photos uploaded at the same time, as the upload screen does."""

import io
import threading
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase
from PIL import Image

from receiving import reader, services

from . import factories as f
from .test_services import content, ok, read_line


def photo(color):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class ParallelUploadTest(TransactionTestCase):
    def test_pages_uploaded_at_once_get_different_numbers(self):
        user = f.user("ana")
        document = services.start_document(user)
        barrier = threading.Barrier(2)
        errors = []

        def upload(color):
            try:
                barrier.wait()
                services.add_page(document, user, photo(color))
            except Exception as e:  # recorded and asserted below
                errors.append(e)
            finally:
                connection.close()

        page = content(1, 1, [read_line("AT-1001", "GARDEN HOSE 1/2")])
        with mock.patch.object(reader, "read_page", return_value=ok(page)):
            threads = [threading.Thread(target=upload, args=(c,)) for c in ("red", "blue")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [])
        self.assertEqual(sorted(document.pages.values_list("upload_order", flat=True)), [1, 2])
