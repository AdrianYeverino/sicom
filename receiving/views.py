"""Screens. They only read and call services: every write goes through
receiving.services, which keeps the rules and the change history.

Phone first: one line per card, the frequent action at the bottom. On a
desktop the review shows the page photos next to the lines.
"""

import csv
import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Product, Supplier

from . import images, pricing, services
from .matching import Catalog
from .models import (
    ConfirmedEntry,
    DocumentPage,
    DocumentStatus,
    DocumentWarning,
    EntryLine,
    LineStatus,
    ProvisionalLine,
    ReceivedDocument,
)
from .verification import proposed_quantity


def _decimal(value):
    """Accepts "1,234.50", "$95" or "95.5" from a phone keyboard."""
    text = (value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise services.ReceivingError(f"«{value}» no es un número.")


def _device(request):
    return request.headers.get("User-Agent", "")[:200]


# --- Lists ----------------------------------------------------------------------


def documents(request):
    status = request.GET.get("status", "")
    found = ReceivedDocument.objects.select_related("supplier", "created_by").annotate(
        flagged=Count("lines", filter=Q(lines__status=LineStatus.FLAGGED)),
        line_count=Count("lines"),
        page_count=Count("pages", distinct=True),
    )
    if status in DocumentStatus.values:
        found = found.filter(status=status)
    return render(request, "receiving/documents.html", {
        "documents": found[:100],
        "status": status,
        "statuses": DocumentStatus.choices,
    })


# --- Upload ---------------------------------------------------------------------


@require_POST
def document_new(request):
    document = services.start_document(request.user)
    return redirect("receiving:upload", document.pk)


def upload(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    if document.status != DocumentStatus.READING:
        return redirect("receiving:review", pk)
    pages = [(page, page.readings.order_by("-created_at").first()) for page in document.pages.all()]
    return render(request, "receiving/upload.html", {"document": document, "pages": pages})


def _page_fragment(request, page, error=""):
    last = page.readings.order_by("-created_at").first()
    return render(request, "receiving/_page.html", {"page": page, "last": last, "error": error})


@require_POST
def page_add(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    upload_file = request.FILES.get("photo")
    if not upload_file:
        return HttpResponseBadRequest("Falta la foto.")
    try:
        page, _ = services.add_page(document, request.user, upload_file.read(), upload_file.name, _device(request))
    except (images.ImageError, services.ReceivingError) as e:
        return render(request, "receiving/_page_error.html", {"name": upload_file.name, "error": str(e)}, status=400)
    return _page_fragment(request, page)


@require_POST
def page_replace(request, page_pk):
    page = get_object_or_404(DocumentPage, pk=page_pk)
    upload_file = request.FILES.get("photo")
    if not upload_file:
        return HttpResponseBadRequest("Falta la foto.")
    try:
        page, _ = services.replace_page(page, request.user, upload_file.read(), upload_file.name, _device(request))
    except (images.ImageError, services.ReceivingError) as e:
        return _page_fragment(request, page, error=str(e))
    return _page_fragment(request, page)


@require_POST
def document_finish(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    try:
        services.finish_reading(document, request.user)
    except services.ReceivingError as e:
        messages.error(request, str(e))
        return redirect("receiving:upload", pk)
    return redirect("receiving:review", pk)


def page_thumbnail(request, page_pk):
    page = get_object_or_404(DocumentPage, pk=page_pk)
    response = HttpResponse(bytes(page.thumbnail), content_type="image/webp")
    response["Cache-Control"] = "private, max-age=86400"
    return response


# --- Review ---------------------------------------------------------------------

LINE_ORDER = {LineStatus.FLAGGED: 0, LineStatus.RESOLVED: 1, LineStatus.CONFIRMED: 1, LineStatus.DISCARDED: 2}


def _history(product):
    """The last purchases of a product, from any supplier."""
    if product is None:
        return []
    return list(
        EntryLine.objects.filter(product=product).select_related("entry__supplier").order_by("-entry__confirmed_at")[:5]
    )


def _line_context(line, catalog=None, error=""):
    suggestion = None
    if line.product is None and line.status != LineStatus.CONFIRMED:
        catalog = catalog or Catalog(line.document.supplier)
        suggestion = catalog.recognize(line.supplier_code, line.description, services.thresholds_of(line.document)).suggestion
    received = line.received_quantity if line.received_quantity is not None else line.quantity
    return {
        "line": line,
        "error": error,
        "suggestion": suggestion,
        "proposed_quantity": proposed_quantity(line),
        "received": received,
        "received_more": received is not None and line.quantity is not None and received > line.quantity,
        "price_options": pricing.options(line.unit_cost, line.product, line.handwritten_price),
        "history": _history(line.product),
        "editable": line.document.status == DocumentStatus.IN_REVIEW,
        "cost_with_tax": (line.unit_cost * (1 + settings.TAX_RATE)).quantize(Decimal("0.01")),
    }


def _summary(document):
    lines = list(document.lines.all())
    received_total = sum(
        (
            (line.received_quantity if line.received_quantity is not None else (line.quantity or 0)) * line.unit_cost
            for line in lines
            if line.status != LineStatus.DISCARDED
        ),
        Decimal("0"),
    )
    return {
        "document": document,
        "blockers": services.blockers(document) if document.status == DocumentStatus.IN_REVIEW else [],
        "flagged": sum(1 for line in lines if line.status == LineStatus.FLAGGED),
        "not_arrived": sum(1 for line in lines if line.status == LineStatus.DISCARDED),
        "line_count": len(lines),
        "received_total": received_total,
    }


def review(request, pk):
    document = get_object_or_404(ReceivedDocument.objects.select_related("supplier", "created_by"), pk=pk)
    if document.status == DocumentStatus.READING:
        return redirect("receiving:upload", pk)
    lines = sorted(
        document.lines.select_related("product", "page"),
        key=lambda line: (LINE_ORDER.get(line.status, 1), line.position),
    )
    for line in lines:
        line.document = document
    catalog = Catalog(document.supplier) if document.status == DocumentStatus.IN_REVIEW else None
    cards = [_line_context(line, catalog) for line in lines]
    return render(request, "receiving/review.html", {
        **_summary(document),
        "cards": cards,
        "unknown": sum(
            1 for c in cards
            if c["line"].product is None and c["suggestion"] is None and c["line"].reason == "unrecognized_product"
        ),
        "pages": document.pages.all(),
        "warnings": [DocumentWarning(w).label for w in document.warnings if w in DocumentWarning.values],
        "amounts": sum((line.amount for line in lines), Decimal("0")),
        "suppliers": Supplier.objects.filter(active=True),
        "entry": ConfirmedEntry.objects.filter(document=document).first(),
    })


def document_summary(request, pk):
    """The bottom bar, refreshed after every line change."""
    return render(request, "receiving/_summary.html", _summary(get_object_or_404(ReceivedDocument, pk=pk)))


def _line_response(request, line, error=""):
    line = ProvisionalLine.objects.select_related("product", "page", "document__supplier").get(pk=line.pk)
    return render(request, "receiving/_line.html", _line_context(line, error=error))


def _line_action(action):
    """Wraps a review action: load the line, run it, answer with the fresh card."""

    @require_POST
    def view(request, line_pk):
        line = get_object_or_404(ProvisionalLine.objects.select_related("document", "product"), pk=line_pk)
        try:
            action(request, line)
        except services.ReceivingError as e:
            return _line_response(request, line, error=str(e))
        response = _line_response(request, line)
        response["HX-Trigger"] = "lineChanged"
        return response

    return view


def _correct(request, line):
    values = {}
    for name in ("quantity", "unit_cost", "amount"):
        if name in request.POST:
            values[name] = _decimal(request.POST[name])
    for name in ("description", "supplier_code", "unit"):
        if name in request.POST:
            values[name] = request.POST[name].strip()
    if "unit_cost" in values and values["unit_cost"] is None:
        raise services.ReceivingError("El costo unitario es obligatorio.")
    if "amount" in values and values["amount"] is None:
        raise services.ReceivingError("El importe es obligatorio.")
    if "description" in values and not values["description"]:
        raise services.ReceivingError("La descripción es obligatoria.")
    services.correct_line(line, request.user, **values)


def _price(request, line):
    if request.POST.get("by") == "margin":
        services.set_price(line, request.user, margin_percent=_decimal(request.POST.get("margin_percent")))
    else:
        services.set_price(line, request.user, retail_price=_decimal(request.POST.get("retail_price")))


def _link(request, line):
    product = get_object_or_404(Product, pk=request.POST.get("product"), active=True)
    services.link_product(line, request.user, product)


line_accept = _line_action(lambda r, line: services.accept_line(line, r.user))
line_not_arrived = _line_action(lambda r, line: services.mark_not_arrived(line, r.user))
line_undo = _line_action(lambda r, line: services.undo_not_arrived(line, r.user))
line_correct = _line_action(_correct)
line_received = _line_action(lambda r, line: services.set_received(line, r.user, _decimal(r.POST.get("received"))))
line_price = _line_action(_price)
line_link = _line_action(_link)
line_new_product = _line_action(
    lambda r, line: services.create_product_for(line, r.user, r.POST.get("name", ""), r.POST.get("unit", ""))
)


def product_search(request, line_pk):
    line = get_object_or_404(ProvisionalLine, pk=line_pk)
    q = request.GET.get("q", "").strip()
    found = Product.objects.filter(active=True)
    if q:
        found = found.filter(
            Q(name__icontains=q) | Q(aliases__alias__icontains=q) | Q(supplier_codes__code__iexact=q)
        ).distinct()
    return render(request, "receiving/_product_results.html", {"line": line, "products": found[:12], "q": q})


@require_POST
def document_supplier(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    try:
        if request.POST.get("supplier"):
            supplier = get_object_or_404(Supplier, pk=request.POST["supplier"])
            services.set_supplier(document, request.user, supplier)
        else:
            services.set_supplier(
                document, request.user, name=request.POST.get("name", ""), rfc=request.POST.get("rfc", "").strip().upper()
            )
    except services.ReceivingError as e:
        messages.error(request, str(e))
    except IntegrityError:
        messages.error(request, "Ya existe un proveedor con ese nombre o RFC, o el RFC no tiene el formato correcto.")
    return redirect("receiving:review", pk)


@require_POST
def document_new_products(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    try:
        created = services.create_products_for_unrecognized(document, request.user)
    except services.ReceivingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"{created} productos dados de alta con su descripción. Revisa los que quedan.")
    return redirect("receiving:review", pk)


@require_POST
def document_confirm(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    try:
        entry = services.confirm(document, request.user)
    except services.ReceivingError as e:
        messages.error(request, str(e))
        return redirect("receiving:review", pk)
    messages.success(request, "Entrada confirmada.")
    return redirect("receiving:entry", entry.pk)


@require_POST
def document_discard(request, pk):
    document = get_object_or_404(ReceivedDocument, pk=pk)
    try:
        services.discard_document(document, request.user)
    except services.ReceivingError as e:
        messages.error(request, str(e))
        return redirect("receiving:review", pk)
    messages.success(request, "Documento descartado. Queda en el historial.")
    return redirect("receiving:documents")


# --- Entries and CSV --------------------------------------------------------------

CSV_HEADER = [
    "fecha", "proveedor", "folio", "producto", "clave_proveedor", "cantidad", "costo_unitario",
    "subtotal", "margen_pct", "precio_publico", "confirmado_por", "confirmado_en",
]


def _csv(lines, filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # Excel reads accents in UTF-8 only with the BOM
    writer = csv.writer(response)
    writer.writerow(CSV_HEADER)
    for line in lines:
        entry = line.entry
        writer.writerow([
            entry.entry_date.isoformat(), entry.supplier.name, entry.folio, line.product.name,
            line.source_line.supplier_code, line.quantity, line.unit_cost, line.subtotal,
            "" if line.margin_percent is None else line.margin_percent,
            "" if line.retail_price is None else line.retail_price,
            entry.confirmed_by.get_username(), entry.confirmed_at.isoformat(timespec="minutes"),
        ])
    return response


def _entry_lines():
    return EntryLine.objects.select_related("entry__supplier", "entry__confirmed_by", "product", "source_line")


def _date(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def entries(request):
    found = ConfirmedEntry.objects.select_related("supplier", "confirmed_by").annotate(line_count=Count("lines"))
    supplier = request.GET.get("supplier", "")
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if supplier:
        found = found.filter(supplier_id=supplier)
    if start:
        found = found.filter(entry_date__gte=start)
    if end:
        found = found.filter(entry_date__lte=end)
    if request.GET.get("format") == "csv":
        lines = _entry_lines().filter(entry__in=found).order_by("entry__entry_date", "entry__folio", "source_line__position")
        return _csv(lines, "entradas.csv")
    found = list(found[:200])
    totals = {e.pk: Decimal("0") for e in found}
    for line in EntryLine.objects.filter(entry__in=found).only("entry_id", "quantity", "unit_cost"):
        totals[line.entry_id] += line.quantity * line.unit_cost
    return render(request, "receiving/entries.html", {
        "rows": [(e, totals[e.pk]) for e in found],
        "suppliers": Supplier.objects.all(),
        "filters": request.GET,
        "query": request.GET.urlencode(),
    })


def entry(request, entry_pk):
    found = get_object_or_404(ConfirmedEntry.objects.select_related("supplier", "confirmed_by", "document"), pk=entry_pk)
    lines = _entry_lines().filter(entry=found).order_by("source_line__position")
    if request.GET.get("format") == "csv":
        return _csv(lines, f"entrada-{found.folio or found.pk}.csv")
    lines = list(lines)
    return render(request, "receiving/entry.html", {
        "entry": found,
        "lines": lines,
        "subtotal": sum((line.subtotal for line in lines), Decimal("0")),
        "not_arrived": found.document.lines.filter(status=LineStatus.DISCARDED),
    })
