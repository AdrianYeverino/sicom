"""The project's goals, measured on confirmed documents.

    python manage.py metrics

What the person confirmed is the reference. Each confirmed line is compared
with what the model read for it (the saved raw reading), so a correction
made during review counts against the reader.

Goals: quantity and cost read right >= 95 %; lines whose arithmetic adds up
>= 90 %; lines confirmed without correction >= 80 %. Lines marked as not
arrived are not errors of the system and are left out.
"""

from django.core.management.base import BaseCommand
from django.db.models import Avg, Sum

from receiving import reader
from receiving.models import DocumentStatus, LineStatus, PageReading, ReceivedDocument
from receiving.services import latest_reading
from receiving.verification import merge


def pct(part, whole):
    return f"{part / whole:.1%}" if whole else "—"


class Command(BaseCommand):
    help = "Measures the reader and the review against the project's goals."

    def handle(self, **options):
        documents = ReceivedDocument.objects.filter(status=DocumentStatus.CONFIRMED)
        lines = qty_ok = cost_ok = matches = untouched = 0
        for document in documents:
            pages = list(document.pages.order_by("upload_order"))
            merged = merge([(p.pk, reader.to_content(latest_reading(p).raw)) for p in pages])
            read = [line for _, line in merged.lines]
            for line in document.lines.filter(status=LineStatus.CONFIRMED).order_by("position"):
                original = read[line.position - 1]
                lines += 1
                qty_ok += original.quantity is not None and original.quantity == line.quantity
                cost_ok += original.unit_cost == line.unit_cost
                matches += bool(line.amount_matches)
                untouched += not line.corrected_by_person

        readings = PageReading.objects.filter(from_cache=False, outcome="ok")
        spent = readings.aggregate(total=Sum("cost_usd"), avg=Avg("cost_usd"), tokens=Avg("input_tokens"))
        self.stdout.write(f"Documentos confirmados: {documents.count()} · renglones confirmados: {lines}")
        self.stdout.write(f"Cantidad leída correcta:       {pct(qty_ok, lines)}  (meta >= 95 %)")
        self.stdout.write(f"Costo leído correcto:          {pct(cost_ok, lines)}  (meta >= 95 %)")
        self.stdout.write(f"Renglones que cuadran:         {pct(matches, lines)}  (meta >= 90 %)")
        self.stdout.write(f"Confirmados sin corrección:    {pct(untouched, lines)}  (meta >= 80 %)")
        if spent["total"]:
            self.stdout.write(
                f"Lecturas pagadas: {readings.count()} · USD {spent['total']:.4f} · "
                f"{spent['avg']:.5f} por página · {spent['tokens']:.0f} tokens de entrada por página"
            )
