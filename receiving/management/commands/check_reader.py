"""Checks that the main and the backup model can read right now.

    python manage.py check_reader

Each model gets one real request, as a page would: the same prompt, the
same strict schema and the same provider rules, with a small blank image.
The expected answer is "not a supplier sheet"; any answer that comes back
inside the schema means the model is reachable. It costs a fraction of a
cent per model. Run it before a demo, or when pages stop being read.
Nothing is written to the database.
"""

import io

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from PIL import Image

from receiving import reader, services
from receiving.models import OUTCOME_HELP, ReadingOutcome, model_name

# Answers that came back inside the schema: the model and a provider work.
REACHABLE = {reader.OK, reader.NOT_A_DOCUMENT}


def blank_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Sends one small request to the main and the backup model and says whether each one answers."

    def handle(self, **options):
        if not settings.OPENROUTER_API_KEY:
            raise CommandError("OPENROUTER_API_KEY is not set.")
        models = [("principal", settings.READER_MODEL)]
        if services.backup_model():
            models.append(("reserva", services.backup_model()))
        else:
            self.stdout.write("Sin modelo de reserva (READER_BACKUP_MODEL vacío).")

        image, failed = blank_jpeg(), 0
        for role, model in models:
            # A saved reading would hide whether the model answers today.
            with override_settings(READER_CACHE_DIR=""):
                result = reader.read_page(image, model=model)
            usage = result.usage
            name = f"{model_name(model)} ({role}, {model})"
            if result.outcome in REACHABLE:
                self.stdout.write(self.style.SUCCESS(
                    f"OK     {name}: responde vía {usage.provider or 'proveedor sin nombre'}, "
                    f"{usage.seconds} s, {usage.cost_usd or 0} USD"
                ))
                continue
            failed += 1
            self.stdout.write(self.style.ERROR(f"FALLA  {name}: {ReadingOutcome(result.outcome).label}"))
            self.stdout.write(f"       {OUTCOME_HELP.get(result.outcome, '')}")
            if result.error_detail:
                self.stdout.write(f"       Detalle: {result.error_detail}")
        if failed == len(models):
            raise CommandError("Ningún modelo pudo responder: no se puede leer ninguna hoja.")
