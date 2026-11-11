"""What reading cost, from the readings already stored: one row per model
call, failed attempts included, because every attempt may have been paid.

A replayed (saved) reading cost nothing this time: its tokens are shown
but its cost is not counted. Pesos use the configured exchange rate.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Count, Q, Sum

from .models import PageReading, model_name


def to_mxn(usd):
    return (Decimal(usd or 0) * settings.USD_MXN_RATE).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass
class Cost:
    calls: int = 0
    failed: int = 0
    replayed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: Decimal = field(default_factory=lambda: Decimal("0"))

    @property
    def tokens(self):
        return self.input_tokens + self.output_tokens

    @property
    def mxn(self):
        return to_mxn(self.usd)


AGGREGATES = {
    "calls": Count("id"),
    "failed": Count("id", filter=~Q(outcome="ok")),
    "replayed": Count("id", filter=Q(from_cache=True)),
    "input_tokens": Sum("input_tokens"),
    "output_tokens": Sum("output_tokens"),
    "usd": Sum("cost_usd", filter=Q(from_cache=False)),
}


def _cost(row):
    return Cost(
        calls=row["calls"] or 0,
        failed=row["failed"] or 0,
        replayed=row["replayed"] or 0,
        input_tokens=row["input_tokens"] or 0,
        output_tokens=row["output_tokens"] or 0,
        usd=row["usd"] or Decimal("0"),
    )


def total(readings=None):
    readings = PageReading.objects.all() if readings is None else readings
    return _cost(readings.aggregate(**AGGREGATES))


def by_document(document_ids):
    """{document id: Cost} in one query."""
    rows = (
        PageReading.objects.filter(page__document_id__in=list(document_ids))
        .values("page__document_id")
        .annotate(**AGGREGATES)
    )
    return {row["page__document_id"]: _cost(row) for row in rows}


def of_document(document):
    return total(PageReading.objects.filter(page__document=document))


@dataclass
class ModelUse:
    name: str
    model: str
    backup_calls: int
    pages_read: int
    cost: Cost


def by_model():
    """What each model did: calls, pages it read, cost, and how many of its
    calls were as the backup. Main model first, then by number of calls."""
    rows = (
        PageReading.objects.values("model")
        .annotate(
            **AGGREGATES,
            backup_calls=Count("id", filter=Q(is_backup=True)),
            pages_read=Count("id", filter=Q(outcome="ok")),
        )
    )
    uses = [
        ModelUse(
            name=model_name(row["model"]),
            model=row["model"],
            backup_calls=row["backup_calls"],
            pages_read=row["pages_read"],
            cost=_cost(row),
        )
        for row in rows
    ]
    return sorted(uses, key=lambda u: (u.model != settings.READER_MODEL, -u.cost.calls))
