"""The project's goals, measured on confirmed documents.

What the person confirmed is the reference. Each confirmed line is compared
with what the model read for it (the saved raw reading), so a correction
made during review counts against the reader. Lines marked as not arrived
are not errors of the system and are left out.
"""

from dataclasses import dataclass

from django.db.models import Avg, Count, Sum

from . import reader
from .models import DocumentStatus, LineStatus, PageReading, ReceivedDocument
from .services import latest_reading
from .verification import merge

GOALS = {"quantity": 0.95, "cost": 0.95, "matches": 0.90, "untouched": 0.80}


@dataclass
class Measurements:
    documents: int = 0
    lines: int = 0
    quantity_ok: int = 0
    cost_ok: int = 0
    matches: int = 0
    untouched: int = 0
    readings_paid: int = 0
    spent_usd: float = 0.0
    cost_per_page: float = 0.0
    tokens_per_page: float = 0.0

    def rows(self):
        """(label, result as text, goal as text, goal met) for each goal."""
        found = []
        for label, part, goal in (
            ("Cantidad leída correcta", self.quantity_ok, GOALS["quantity"]),
            ("Costo leído correcto", self.cost_ok, GOALS["cost"]),
            ("Renglones que cuadran", self.matches, GOALS["matches"]),
            ("Confirmados sin corrección", self.untouched, GOALS["untouched"]),
        ):
            value = part / self.lines if self.lines else None
            shown = "—" if value is None else f"{value:.1%}"
            found.append((label, shown, f"{goal:.0%}", value is not None and value >= goal))
        return found


def measure():
    result = Measurements()
    documents = ReceivedDocument.objects.filter(status=DocumentStatus.CONFIRMED)
    result.documents = documents.count()
    for document in documents.prefetch_related("pages"):
        pages = list(document.pages.order_by("upload_order"))
        merged = merge([(p.pk, reader.to_content(latest_reading(p).raw)) for p in pages])
        read = [line for _, line in merged.lines]
        for line in document.lines.filter(status=LineStatus.CONFIRMED).order_by("position"):
            if line.position > len(read):
                continue
            original = read[line.position - 1]
            result.lines += 1
            result.quantity_ok += original.quantity is not None and original.quantity == line.quantity
            result.cost_ok += original.unit_cost == line.unit_cost
            result.matches += bool(line.amount_matches)
            result.untouched += not line.corrected_by_person

    paid = PageReading.objects.filter(from_cache=False, outcome="ok").aggregate(
        n=Count("id"), total=Sum("cost_usd"), avg=Avg("cost_usd"), tokens=Avg("input_tokens")
    )
    result.readings_paid = paid["n"] or 0
    result.spent_usd = float(paid["total"] or 0)
    result.cost_per_page = float(paid["avg"] or 0)
    result.tokens_per_page = float(paid["tokens"] or 0)
    return result
