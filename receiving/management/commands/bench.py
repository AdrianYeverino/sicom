"""Runs a folder of real sheets through the whole flow and reports.

    python manage.py bench <folder> [--live] [--max-side 2000] [--user dev]

Every folder that holds photos is one document; its photos are its pages,
in name order. With READER_CACHE_DIR set, a photo already read at the same
size is replayed for free; --live reads every page again. The documents
stay in review in the local database, to be reviewed and confirmed in the
app: what the person confirms is the reference for `manage.py metrics`.

The photos and readings are business data: keep them outside the repository.
"""

from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings

from receiving import services
from receiving.models import LineStatus, ReadStatus

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def documents_in(root):
    folders = {}
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() in PHOTO_SUFFIXES:
            folders.setdefault(path.parent, []).append(path)
    return folders


class Command(BaseCommand):
    help = "Runs a folder of supplier sheets through reading and verification, and reports."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--live", action="store_true", help="read every page again, ignoring saved readings")
        parser.add_argument("--max-side", type=int, default=None, help="long side of the image sent to the model")
        parser.add_argument("--user", default=None, help="username the documents are uploaded as")
        parser.add_argument("--only", default="", help="only folders whose path contains this text")
        parser.add_argument("--skip", default="", help="leave out photos whose name contains this text (e.g. Copia)")

    def handle(self, folder, live, max_side, user, only, skip, **options):
        root = Path(folder)
        if not root.is_dir():
            raise CommandError(f"{root} is not a folder.")
        users = get_user_model().objects
        person = users.get(username=user) if user else users.filter(is_superuser=True).order_by("date_joined").first()
        if person is None:
            raise CommandError("Create a user first (createsuperuser), or pass --user.")

        overrides = {}
        if max_side:
            overrides["READER_IMAGE_MAX_SIDE"] = max_side
        if live:
            overrides["READER_CACHE_DIR"] = ""
        found = {}
        for leaf, photos in documents_in(root).items():
            photos = [p for p in photos if not (skip and skip.lower() in p.name.lower())]
            if photos and only.lower() in str(leaf).lower():
                found[leaf] = photos
        if not found:
            raise CommandError("No photos found.")

        totals = Counter()
        cost = Decimal("0")
        with override_settings(**overrides):
            side = settings.READER_IMAGE_MAX_SIDE
            self.stdout.write(f"{len(found)} documents · image side {side}px · {'live' if live else 'saved readings allowed'}\n")
            for leaf, photos in found.items():
                name = str(leaf.relative_to(root))
                document = services.start_document(person)
                for photo in photos:
                    services.add_page(document, person, photo.read_bytes(), photo.name, "bench")
                pages = list(document.pages.all())
                readings = [r for p in pages for r in p.readings.all()]
                doc_cost = sum((r.cost_usd or Decimal("0") for r in readings if not r.from_cache), Decimal("0"))
                cost += doc_cost
                cached = sum(1 for r in readings if r.from_cache)
                if any(p.read_status != ReadStatus.READ for p in pages):
                    failed = [r.outcome for r in readings if r.outcome != "ok"]
                    self.stdout.write(self.style.ERROR(f"FALLÓ {name}: page not read {failed}"))
                    totals["failed_documents"] += 1
                    continue
                document = services.finish_reading(document, person)
                lines = list(document.lines.all())
                reasons = Counter(line.reason for line in lines if line.status == LineStatus.FLAGGED)
                matches = sum(1 for line in lines if line.amount_matches)
                totals["documents"] += 1
                totals["lines"] += len(lines)
                totals["matches"] += matches
                totals["flagged"] += sum(reasons.values())
                for reason, count in reasons.items():
                    totals[f"reason:{reason}"] += count
                tokens_in = sum(r.input_tokens or 0 for r in readings if r.outcome == "ok")
                self.stdout.write(
                    f"OK {name}: {len(pages)} pág · {len(lines)} renglones · cuadran {matches}/{len(lines)} · "
                    f"separados {dict(reasons) or 0} · avisos {document.warnings or '-'} · "
                    f"tokens entrada {tokens_in} · USD {doc_cost:.5f}{f' ({cached} guardadas)' if cached else ''}"
                )

        self.stdout.write("")
        lines = totals["lines"] or 1
        self.stdout.write(
            f"Total: {totals['documents']} documentos, {totals['lines']} renglones, "
            f"cuadran {totals['matches'] / lines:.1%}, separados {totals['flagged'] / lines:.1%}, "
            f"fallidos {totals['failed_documents']}, costo de esta corrida USD {cost:.4f}"
        )
        for key in sorted(k for k in totals if k.startswith("reason:")):
            self.stdout.write(f"  {key[7:]}: {totals[key]}")
