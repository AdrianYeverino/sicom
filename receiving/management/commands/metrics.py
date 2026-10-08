"""The project's goals, measured on confirmed documents (see receiving.measurements).

    python manage.py metrics
"""

from django.core.management.base import BaseCommand

from receiving.measurements import measure


class Command(BaseCommand):
    help = "Measures the reader and the review against the project's goals."

    def handle(self, **options):
        m = measure()
        self.stdout.write(f"Documentos confirmados: {m.documents} · renglones confirmados: {m.lines}")
        for label, shown, goal, _ in m.rows():
            self.stdout.write(f"{label + ':':<31}{shown}  (meta >= {goal})")
        if m.readings_paid:
            self.stdout.write(
                f"Lecturas pagadas: {m.readings_paid} · USD {m.spent_usd:.4f} · "
                f"{m.cost_per_page:.5f} por página · {m.tokens_per_page:.0f} tokens de entrada por página"
            )
