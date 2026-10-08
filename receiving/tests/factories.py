"""Invented test data. Never real sheets: the repository is public."""

from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model

from receiving.models import DocumentPage, ProvisionalLine, ReceivedDocument

_seq = count(1)


def user(username=None):
    return get_user_model().objects.create_user(username or f"user{next(_seq)}", password="x-test-pass-123")


def document(created_by=None, **fields):
    return ReceivedDocument.objects.create(created_by=created_by or user(), **fields)


def page(doc, order=1, **fields):
    values = {
        "upload_order": order,
        "file_sha256": f"{next(_seq):064x}",
        "width": 3024,
        "height": 4032,
        "file_size": 1_500_000,
        "thumbnail": b"webp",
        "uploaded_by": doc.created_by,
    }
    values.update(fields)
    return DocumentPage.objects.create(document=doc, **values)


def line(doc, pg=None, **fields):
    values = {
        "page": pg or page(doc, order=doc.pages.count() + 1),
        "position": doc.lines.count() + 1,
        "quantity": Decimal("2"),
        "description": "GARDEN HOSE 1/2",
        "unit_cost": Decimal("40.50"),
        "amount": Decimal("81.00"),
        "confidence": Decimal("1"),
        "amount_matches": True,
    }
    values.update(fields)
    return ProvisionalLine.objects.create(document=doc, **values)
