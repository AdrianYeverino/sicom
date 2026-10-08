"""Everything that writes receiving data goes through here, so the rules and
the change history hold no matter which screen calls them.

Flow: start_document → add_page (one call per photo: prepare, read) →
finish_reading (merge, recognize, verify) → review operations → confirm.
"""

import time
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import CostSource, Product, ProductAlias, ProductSupplierCode, Supplier
from core.history import Source, changes_by

from . import images, pricing, reader
from .matching import Catalog, find_supplier
from .models import (
    ConfirmedEntry,
    DocumentPage,
    DocumentStatus,
    DocumentWarning,
    EntryLine,
    LineReason,
    LineStatus,
    PageReading,
    ProvisionalLine,
    ReadingOutcome,
    ReadStatus,
    ReceivedDocument,
)
from .verification import adds_up, check_document, check_line, merge

RETRY_PAUSES = (2, 5)  # seconds before the second and third attempt


class ReceivingError(Exception):
    """A rule the person can fix. The message is for them."""


# --- Upload and reading -------------------------------------------------------


def start_document(user):
    with changes_by(user, Source.REVIEW):
        return ReceivedDocument.objects.create(created_by=user, thresholds=_thresholds())


def _thresholds():
    return {name: str(value) for name, value in settings.THRESHOLDS.items()}


def thresholds_of(document):
    """The values this document is checked with: the ones stored on it,
    completed with the current settings for any that are missing."""
    values = dict(settings.THRESHOLDS)
    values.update({name: Decimal(value) for name, value in (document.thresholds or {}).items()})
    return values


def _record(page, result, image_side):
    return PageReading.objects.create(
        page=page,
        outcome=result.outcome,
        error_detail=result.error_detail,
        model=result.usage.model,
        provider=result.usage.provider or "",
        prompt_version=result.prompt_version,
        image_side=image_side,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_usd=result.usage.cost_usd,
        seconds=result.usage.seconds,
        from_cache=result.from_cache,
        raw=result.raw,
    )


def read_with_retries(page, model_jpeg, image_side, pause=time.sleep):
    """Reads one page, retrying only what can succeed on a second try.
    Every attempt is recorded: each one may have been paid for."""
    for attempt in range(len(RETRY_PAUSES) + 1):
        result = reader.read_page(model_jpeg)
        _record(page, result, image_side)
        if result.outcome not in reader.RETRYABLE or attempt == len(RETRY_PAUSES):
            break
        pause(RETRY_PAUSES[attempt])
    page.read_status = ReadStatus.READ if result.ok else ReadStatus.FAILED
    page.save(update_fields=["read_status", "updated_at"])
    return result


def add_page(document, user, data, filename="", device=""):
    """One photo: prepare it, keep its trace and thumbnail, read it. The photo
    itself is not stored; to read a page again, upload it again."""
    if document.status != DocumentStatus.READING:
        raise ReceivingError("Este documento ya no acepta páginas.")
    prepared = images.prepare(data)  # raises images.ImageError
    with transaction.atomic():
        # Photos upload in parallel: lock the document so two pages never
        # take the same number.
        document = ReceivedDocument.objects.select_for_update().get(pk=document.pk)
        if document.status != DocumentStatus.READING:
            raise ReceivingError("Este documento ya no acepta páginas.")
        order = (document.pages.order_by("-upload_order").values_list("upload_order", flat=True).first() or 0) + 1
        page = DocumentPage.objects.create(
            document=document,
            upload_order=order,
            original_filename=filename[:255],
            file_sha256=prepared.sha256,
            captured_at=prepared.captured_at,
            width=prepared.width,
            height=prepared.height,
            file_size=prepared.file_size,
            thumbnail=prepared.thumbnail_webp,
            device=device[:200],
            uploaded_by=user,
        )
    result = read_with_retries(page, prepared.model_jpeg, prepared.model_side)
    return page, result


def replace_page(page, user, data, filename="", device=""):
    """A failed page uploaded again keeps its place in the document."""
    if page.document.status != DocumentStatus.READING:
        raise ReceivingError("Este documento ya no acepta páginas.")
    prepared = images.prepare(data)
    page.original_filename = filename[:255]
    page.file_sha256 = prepared.sha256
    page.captured_at = prepared.captured_at
    page.width, page.height, page.file_size = prepared.width, prepared.height, prepared.file_size
    page.thumbnail = prepared.thumbnail_webp
    page.device = device[:200]
    page.save()
    return page, read_with_retries(page, prepared.model_jpeg, prepared.model_side)


def latest_reading(page):
    return page.readings.filter(outcome=ReadingOutcome.OK).order_by("-created_at").first()


def finish_reading(document, user):
    """All pages read: merge them, recognize the products and verify every
    line. The document moves to review."""
    pages = list(document.pages.order_by("upload_order"))
    if not pages:
        raise ReceivingError("Sube al menos una página.")
    if any(p.read_status != ReadStatus.READ for p in pages):
        raise ReceivingError("Hay páginas sin leer. Vuelve a subirlas o quítalas.")

    contents = [(page.pk, reader.to_content(latest_reading(page).raw)) for page in pages]
    merged = merge(contents)
    thresholds = thresholds_of(document)
    by_id = {p.pk: p for p in pages}

    with transaction.atomic(), changes_by(user, Source.READER):
        document = ReceivedDocument.objects.select_for_update().get(pk=document.pk)
        document.supplier_name_read = merged.supplier_name[:200]
        document.supplier_rfc_read = merged.supplier_rfc[:13]
        document.document_type_read = merged.document_type[:60]
        document.folio_read = merged.folio[:60]
        document.document_date_read = merged.date
        document.subtotal_read, document.tax_read, document.total_read = merged.subtotal, merged.tax, merged.total
        document.printed_page_count = merged.page_count
        for content_key, content in contents:
            by_id[content_key].printed_page = content.page_number
            by_id[content_key].save(update_fields=["printed_page", "updated_at"])
        document.supplier = find_supplier(merged.supplier_name, merged.supplier_rfc)
        catalog = Catalog(document.supplier)

        for position, (page_id, line) in enumerate(merged.lines, start=1):
            recognition = catalog.recognize(line.supplier_code, line.description, thresholds)
            product = recognition.product
            check = check_line(line, recognition, product.last_cost if product else None, thresholds)
            ProvisionalLine.objects.create(
                document=document,
                page=by_id[page_id],
                position=position,
                quantity=line.quantity,
                unit=line.unit[:30],
                supplier_code=line.supplier_code[:60],
                description=line.description[:300] or "(sin descripción)",
                unit_cost=line.unit_cost,
                discount_percent=line.discount_percent,
                amount=line.amount,
                handwritten_price=line.handwritten_price,
                confidence=min(max(line.confidence, Decimal("0")), Decimal("1")),
                amount_matches=check.amount_matches,
                status=check.status,
                reason=check.reason,
                product=product,
            )

        warnings = check_document(merged, thresholds, settings.TAX_RATE)
        if _duplicate_folio(document, merged.folio):
            warnings.append(str(DocumentWarning.DUPLICATE_DOCUMENT))
        if DocumentPage.objects.filter(file_sha256__in=[p.file_sha256 for p in pages]).exclude(document=document).exists():
            warnings.append(str(DocumentWarning.DUPLICATE_PHOTO))
        document.warnings = warnings
        document.status = DocumentStatus.IN_REVIEW
        document.save()
    return document


def _duplicate_folio(document, folio):
    if not folio:
        return False
    others = ReceivedDocument.objects.filter(folio_read__iexact=folio).exclude(pk=document.pk).exclude(
        status=DocumentStatus.DISCARDED
    )
    if document.supplier:
        others = others.filter(supplier=document.supplier)
    return others.exists()


# --- Review -------------------------------------------------------------------

READ_FIELDS = ("quantity", "unit", "supplier_code", "description", "unit_cost", "discount_percent", "amount")


def _editable(line):
    if line.document.status != DocumentStatus.IN_REVIEW:
        raise ReceivingError("Este documento ya no se puede modificar.")


def correct_line(line, user, **values):
    """The person fixes what was read. A flagged line they have looked at and
    fixed counts as resolved."""
    _editable(line)
    with changes_by(user, Source.REVIEW):
        changed = False
        for name in READ_FIELDS:
            if name in values and values[name] != getattr(line, name):
                setattr(line, name, values[name])
                changed = True
        if changed:
            line.corrected_by_person = True
        if line.quantity is not None:
            tolerance = thresholds_of(line.document)["amount_tolerance"]
            line.amount_matches = adds_up(line.quantity, line.unit_cost, line.discount_percent, line.amount, tolerance)
        else:
            line.amount_matches = None
        _settle(line)
        line.save()
    return line


def _settle(line):
    """After a person's action: a line with everything it needs stops being flagged."""
    if line.status in (LineStatus.DISCARDED, LineStatus.CONFIRMED):
        return
    if line.quantity is not None and line.product_id:
        if line.status == LineStatus.FLAGGED:
            line.status = LineStatus.RESOLVED
            line.reason = LineReason.NONE
            line.corrected_by_person = True


def accept_line(line, user):
    """The person looked at a flagged line and what was read is right."""
    _editable(line)
    if line.quantity is None:
        raise ReceivingError("Falta la cantidad: escríbela antes de aceptar.")
    if not line.product_id:
        raise ReceivingError("Liga el renglón a un producto o da de alta uno nuevo.")
    with changes_by(user, Source.REVIEW):
        line.status = LineStatus.RESOLVED
        line.reason = LineReason.NONE
        line.save()
    return line


def link_product(line, user, product):
    _editable(line)
    with changes_by(user, Source.REVIEW):
        if line.product_id != product.pk:
            line.corrected_by_person = line.corrected_by_person or line.status == LineStatus.FLAGGED or bool(line.product_id)
        line.product = product
        _settle(line)
        line.save()
    return line


def create_product_for(line, user, name, unit=""):
    """A product seen for the first time, named by the person."""
    _editable(line)
    name = (name or "").strip()
    if not name:
        raise ReceivingError("El producto necesita un nombre.")
    with changes_by(user, Source.REVIEW):
        product = Product.objects.create(name=name[:200], unit=(unit or line.unit)[:30])
    return link_product(line, user, product)


def create_products_for_unrecognized(document, user):
    """First delivery of a supplier: every unknown product becomes a new
    product named as printed, in one tap. Lines with a likely match are left
    alone, so a product is never duplicated by accident."""
    if document.status != DocumentStatus.IN_REVIEW:
        raise ReceivingError("Este documento ya no se puede modificar.")
    catalog = Catalog(document.supplier)
    thresholds = thresholds_of(document)
    created = 0
    lines = document.lines.filter(
        status=LineStatus.FLAGGED, reason=LineReason.UNRECOGNIZED_PRODUCT, product__isnull=True
    ).order_by("position")
    with transaction.atomic():
        for line in lines:
            if catalog.recognize(line.supplier_code, line.description, thresholds).suggestion:
                continue
            create_product_for(line, user, line.description, line.unit)
            created += 1
    return created


def mark_not_arrived(line, user):
    _editable(line)
    with changes_by(user, Source.REVIEW):
        line.status = LineStatus.DISCARDED
        line.received_quantity = Decimal("0")
        line.save()
    return line


def undo_not_arrived(line, user):
    _editable(line)
    with changes_by(user, Source.REVIEW):
        line.received_quantity = None
        line.status = LineStatus.RESOLVED if line.product_id and line.quantity is not None else LineStatus.FLAGGED
        line.reason = LineReason.NONE if line.status == LineStatus.RESOLVED else (
            LineReason.MISSING_VALUE if line.quantity is None else LineReason.UNRECOGNIZED_PRODUCT
        )
        line.save()
    return line


def set_received(line, user, quantity):
    _editable(line)
    if quantity is None or quantity < 0:
        raise ReceivingError("La cantidad recibida no puede ser negativa.")
    if quantity == 0:
        return mark_not_arrived(line, user)
    with changes_by(user, Source.REVIEW):
        line.received_quantity = quantity
        if line.status == LineStatus.DISCARDED:
            line.status = LineStatus.RESOLVED if line.product_id else LineStatus.FLAGGED
            if line.status == LineStatus.FLAGGED:
                line.reason = LineReason.UNRECOGNIZED_PRODUCT
        line.save()
    return line


def set_price(line, user, margin_percent=None, retail_price=None):
    """Margin and price move together: give one and the other follows."""
    _editable(line)
    if margin_percent is None and retail_price is None:
        raise ReceivingError("Escribe el margen o el precio.")
    with changes_by(user, Source.REVIEW):
        if retail_price is not None:
            line.retail_price = retail_price
            line.margin_percent = pricing.margin_for(line.net_unit_cost, retail_price)
        else:
            line.margin_percent = margin_percent
            line.retail_price = pricing.price_for(line.net_unit_cost, margin_percent)
        if line.margin_percent is not None and line.margin_percent < 0:
            raise ReceivingError("Ese precio queda por debajo del costo con IVA.")
        line.save()
    return line


def set_supplier(document, user, supplier=None, name="", rfc=""):
    """Choose an existing supplier, or create one from what was read."""
    if document.status != DocumentStatus.IN_REVIEW:
        raise ReceivingError("Este documento ya no se puede modificar.")
    with changes_by(user, Source.REVIEW):
        if supplier is None:
            name = (name or document.supplier_name_read).strip()
            if not name:
                raise ReceivingError("El proveedor necesita un nombre.")
            supplier = Supplier.objects.create(name=name[:120], rfc=(rfc or document.supplier_rfc_read)[:13])
        document.supplier = supplier
        document.save()
    return document


def discard_document(document, user):
    if document.status == DocumentStatus.CONFIRMED:
        raise ReceivingError("Un documento confirmado no se puede descartar.")
    with changes_by(user, Source.REVIEW):
        document.status = DocumentStatus.DISCARDED
        document.save()
    return document


# --- Confirmation -------------------------------------------------------------


def blockers(document):
    """What still stops confirming, in words for the person."""
    found = []
    if document.supplier_id is None:
        found.append("Elige o da de alta el proveedor.")
    lines = list(document.lines.all())
    flagged = sum(1 for line in lines if line.status == LineStatus.FLAGGED)
    if flagged:
        found.append("Queda 1 renglón separado por revisar." if flagged == 1 else f"Quedan {flagged} renglones separados por revisar.")
    if lines and all(line.status == LineStatus.DISCARDED for line in lines):
        found.append("No llegó ningún renglón: descarta el documento en lugar de confirmarlo.")
    return found


def confirm(document, user):
    """One transaction: the entry and its lines, the products' cost, margin
    and price, and the supplier codes learned. All or nothing."""
    with transaction.atomic():
        document = ReceivedDocument.objects.select_for_update().get(pk=document.pk)
        if document.status != DocumentStatus.IN_REVIEW:
            raise ReceivingError("Este documento ya fue confirmado o descartado.")
        problems = blockers(document)
        if problems:
            raise ReceivingError(" ".join(problems))

        with changes_by(user, Source.CONFIRM):
            entry = ConfirmedEntry.objects.create(
                document=document,
                supplier=document.supplier,
                folio=document.folio_read,
                entry_date=document.document_date_read or timezone.localdate(),
                printed_total=document.total_read,
                confirmed_by=user,
            )
            for line in document.lines.select_related("product").order_by("position"):
                if line.status == LineStatus.DISCARDED:
                    continue
                received = line.received_quantity if line.received_quantity is not None else line.quantity
                EntryLine.objects.create(
                    entry=entry,
                    source_line=line,
                    product=line.product,
                    quantity=received,
                    unit_cost=line.net_unit_cost,
                    margin_percent=line.margin_percent,
                    retail_price=line.retail_price,
                )
                _learn(line, document.supplier)
                line.received_quantity = received
                line.status = LineStatus.CONFIRMED
                line.save()
            document.status = DocumentStatus.CONFIRMED
            document.save()
    return entry


def _learn(line, supplier):
    """What the next sheet of this supplier will recognize on its own."""
    product = Product.objects.select_for_update().get(pk=line.product_id)
    product.last_cost = line.net_unit_cost
    product.cost_source = CostSource.SUPPLIER
    if line.margin_percent is not None:
        product.margin_percent = line.margin_percent
    if line.retail_price is not None:
        product.retail_price = line.retail_price
    product.save()

    if line.supplier_code:
        code, created = ProductSupplierCode.objects.get_or_create(
            supplier=supplier,
            code=line.supplier_code,
            defaults={"product": product, "supplier_description": line.description},
        )
        if not created and (code.product_id != product.pk or code.supplier_description != line.description):
            # The person's link wins over what the code pointed to before.
            code.product = product
            code.supplier_description = line.description
            code.save()
    elif not ProductAlias.objects.filter(product=product, alias__iexact=line.description).exists():
        ProductAlias.objects.create(product=product, alias=line.description[:300])
