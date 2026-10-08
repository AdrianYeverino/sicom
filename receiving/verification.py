"""Merging the pages of a document and checking its numbers. Pure functions:
no model, no database. Every decision here can be tested and explained.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from .models import DocumentWarning, LineReason, LineStatus


@dataclass
class MergedDocument:
    supplier_name: str = ""
    supplier_rfc: str = ""
    document_type: str = ""
    folio: str = ""
    date: datetime.date | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    page_count: int | None = None
    missing_pages: list[int] = field(default_factory=list)
    lines: list = field(default_factory=list)  # (page key, ReadLine)


def _date(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def merge(pages):
    """pages: [(key, PageContent)] in upload order.

    The header comes from the first page that has it, the totals from the
    last page that has them, and the lines from every page in order. When
    every page prints its number, that order wins over the upload order.
    """
    if pages and all(content.page_number for _, content in pages):
        pages = sorted(pages, key=lambda item: item[1].page_number)
    merged = MergedDocument()
    for key, content in pages:
        for name, value in (
            ("supplier_name", content.supplier_name),
            ("supplier_rfc", content.supplier_rfc),
            ("document_type", content.document_type),
            ("folio", content.folio),
        ):
            if value and not getattr(merged, name):
                setattr(merged, name, value)
        if merged.date is None:
            merged.date = _date(content.date)
        if any(v is not None for v in (content.subtotal, content.tax, content.total)):
            merged.subtotal, merged.tax, merged.total = content.subtotal, content.tax, content.total
        if content.page_count:
            merged.page_count = max(merged.page_count or 0, content.page_count)
        merged.lines.extend((key, line) for line in content.lines)
    if merged.page_count:
        seen = {content.page_number for _, content in pages if content.page_number}
        merged.missing_pages = [n for n in range(1, merged.page_count + 1) if n not in seen]
    return merged


@dataclass
class LineCheck:
    status: str
    reason: str
    amount_matches: bool | None


def check_line(line, recognition, last_cost, thresholds):
    """The decision for one line. The rules run in order and the first one
    that fails is the reason shown."""
    tolerance = thresholds["amount_tolerance"]
    if line.quantity is None:
        return LineCheck(LineStatus.FLAGGED, LineReason.MISSING_VALUE, None)
    matches = abs(line.quantity * line.unit_cost - line.amount) <= tolerance
    if not matches:
        return LineCheck(LineStatus.FLAGGED, LineReason.AMOUNT_MISMATCH, False)
    if line.confidence < thresholds["min_confidence"]:
        return LineCheck(LineStatus.FLAGGED, LineReason.ILLEGIBLE_TEXT, True)
    if recognition.code_mismatch:
        return LineCheck(LineStatus.FLAGGED, LineReason.CODE_MISMATCH, True)
    if recognition.product is None:
        return LineCheck(LineStatus.FLAGGED, LineReason.UNRECOGNIZED_PRODUCT, True)
    if last_cost and abs(line.unit_cost - last_cost) / last_cost > thresholds["cost_variation"]:
        return LineCheck(LineStatus.RESOLVED, LineReason.COST_VARIATION, True)
    return LineCheck(LineStatus.RESOLVED, LineReason.NONE, True)


def proposed_quantity(line):
    """For a hidden quantity: amount ÷ cost, when it comes out whole enough."""
    if line.quantity is not None or not line.unit_cost:
        return None
    value = (line.amount / line.unit_cost).quantize(Decimal("0.001"))
    return value if value > 0 else None


def check_document(merged, thresholds, tax_rate, today=None):
    """Warnings about the sheet as a whole. None of them blocks confirming."""
    today = today or datetime.date.today()
    warnings = []
    if merged.missing_pages:
        warnings.append(DocumentWarning.MISSING_PAGES)
    amounts = sum((line.amount for _, line in merged.lines), Decimal("0"))
    tolerance = thresholds["subtotal_tolerance"]
    if merged.subtotal is not None and abs(amounts - merged.subtotal) > tolerance:
        warnings.append(DocumentWarning.SUBTOTAL_MISMATCH)
    if None not in (merged.subtotal, merged.tax, merged.total) and abs(merged.subtotal + merged.tax - merged.total) > tolerance:
        warnings.append(DocumentWarning.TOTAL_MISMATCH)
    if merged.subtotal is not None and merged.tax is not None:
        # One peso of room: some systems round the tax per line.
        if abs(merged.subtotal * tax_rate - merged.tax) > Decimal("1.00"):
            warnings.append(DocumentWarning.TAX_MISMATCH)
    if merged.date and not (today - datetime.timedelta(days=366) <= merged.date <= today + datetime.timedelta(days=1)):
        warnings.append(DocumentWarning.UNUSUAL_DATE)
    return [str(w) for w in warnings]
