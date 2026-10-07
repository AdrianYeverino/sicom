"""What the reader saw on a supplier sheet, what the person corrected, and
the entry that is created once it is confirmed.

ReceivedSheet and ProvisionalLine hold the provisional state: nothing in
them touches the catalog. ConfirmedEntry and EntryLine are the final record,
created only from approved lines; in the store system they map onto a
purchase and its lines.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import Product, Supplier
from core.db import MONEY, QUANTITY, BaseModel, not_blank, one_of


class SheetStatus(models.TextChoices):
    READ = "read", "Leída"
    IN_REVIEW = "in_review", "En revisión"
    CONFIRMED = "confirmed", "Confirmada"
    DISCARDED = "discarded", "Descartada"


class ReceivedSheet(BaseModel):
    """One photographed supplier sheet and the header the reader found on it."""

    image = models.FileField("foto", upload_to="sheets/%Y/%m/", blank=True)
    # As printed on the sheet. The supplier itself is chosen during review.
    supplier_name_read = models.CharField("proveedor leído", max_length=200, blank=True)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="sheets", verbose_name="proveedor"
    )
    document_type = models.CharField("tipo de documento", max_length=60, blank=True)
    folio = models.CharField("folio", max_length=60, blank=True)
    sheet_date = models.DateField("fecha de la hoja", null=True, blank=True)
    subtotal_read = models.DecimalField("subtotal leído", **MONEY, null=True, blank=True)
    total_read = models.DecimalField("total leído", **MONEY, null=True, blank=True)
    # What the reading cost, as reported by OpenRouter, and the raw answer to audit it.
    model_used = models.CharField("modelo", max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField("tokens de entrada", null=True, blank=True)
    output_tokens = models.PositiveIntegerField("tokens de salida", null=True, blank=True)
    cost_usd = models.DecimalField("costo USD", max_digits=10, decimal_places=6, null=True, blank=True)
    seconds = models.DecimalField("segundos", max_digits=7, decimal_places=2, null=True, blank=True)
    raw_reading = models.JSONField("lectura cruda", null=True, blank=True)
    status = models.CharField("estado", max_length=12, choices=SheetStatus, default=SheetStatus.READ)
    created_at = models.DateTimeField("creada en", auto_now_add=True)
    confirmed_at = models.DateTimeField("confirmada en", null=True, blank=True)

    class Meta:
        db_table = "received_sheets"
        verbose_name = "hoja recibida"
        verbose_name_plural = "hojas recibidas"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=one_of("status", SheetStatus.values), name="received_sheets_status_valid"),
            models.CheckConstraint(
                condition=Q(status=SheetStatus.CONFIRMED, confirmed_at__isnull=False)
                | (~Q(status=SheetStatus.CONFIRMED) & Q(confirmed_at__isnull=True)),
                name="received_sheets_confirmed_has_date",
            ),
            models.CheckConstraint(
                condition=Q(cost_usd__isnull=True) | Q(cost_usd__gte=0), name="received_sheets_cost_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.supplier or self.supplier_name_read or 'Hoja'} {self.folio}".strip()


class LineStatus(models.TextChoices):
    RESOLVED = "resolved", "Resuelto"
    FLAGGED = "flagged", "Separado"
    CONFIRMED = "confirmed", "Confirmado"
    DISCARDED = "discarded", "Descartado"


class LineReason(models.TextChoices):
    """Why a line was set apart. Checked in this order; the first that fails is shown."""

    NONE = "", "Ninguno"
    AMOUNT_MISMATCH = "amount_mismatch", "El importe no coincide"
    ILLEGIBLE_TEXT = "illegible_text", "Texto ilegible"
    UNRECOGNIZED_PRODUCT = "unrecognized_product", "Producto no reconocido"
    COST_VARIATION = "cost_variation", "Variación de costo"


# Reasons that send a line to the exceptions tray. A cost variation is only a warning.
FLAG_REASONS = [LineReason.AMOUNT_MISMATCH, LineReason.ILLEGIBLE_TEXT, LineReason.UNRECOGNIZED_PRODUCT]


class ProvisionalLine(BaseModel):
    """One line as the reader saw it, plus what the person corrects while counting."""

    sheet = models.ForeignKey(ReceivedSheet, on_delete=models.PROTECT, related_name="lines", verbose_name="hoja")
    position = models.PositiveSmallIntegerField("renglón")
    # Read from the sheet, as printed.
    quantity = models.DecimalField("cantidad", **QUANTITY)
    unit = models.CharField("unidad", max_length=30, blank=True)
    supplier_code = models.CharField("clave del proveedor", max_length=60, blank=True)
    description = models.CharField("descripción", max_length=300)
    unit_cost = models.DecimalField("costo unitario", **MONEY)
    # Only to check the sheet's arithmetic: the entry recalculates its own subtotal.
    amount = models.DecimalField("importe", **MONEY)
    confidence = models.DecimalField("confianza", max_digits=4, decimal_places=3)
    amount_matches = models.BooleanField("cuadra")
    # The verifier's decision.
    status = models.CharField("estado", max_length=10, choices=LineStatus, default=LineStatus.RESOLVED)
    reason = models.CharField("motivo", max_length=24, choices=LineReason, default=LineReason.NONE, blank=True)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="producto"
    )
    # Captured by the person during review.
    received_quantity = models.DecimalField("cantidad recibida", **QUANTITY, null=True, blank=True)
    retail_price = models.DecimalField("precio público", **MONEY, null=True, blank=True)
    # Measures the goal of lines resolved without correction.
    corrected_by_person = models.BooleanField("corregido por la persona", default=False)

    class Meta:
        db_table = "provisional_lines"
        verbose_name = "renglón provisional"
        verbose_name_plural = "renglones provisionales"
        ordering = ["sheet", "position"]
        constraints = [
            models.UniqueConstraint(fields=["sheet", "position"], name="provisional_lines_position_unique"),
            models.CheckConstraint(condition=one_of("status", LineStatus.values), name="provisional_lines_status_valid"),
            models.CheckConstraint(condition=one_of("reason", LineReason.values), name="provisional_lines_reason_valid"),
            # A flagged line always says why; a resolved one at most carries the cost warning.
            models.CheckConstraint(
                condition=~Q(status=LineStatus.FLAGGED) | one_of("reason", FLAG_REASONS),
                name="provisional_lines_flagged_has_reason",
            ),
            models.CheckConstraint(
                condition=~Q(status=LineStatus.RESOLVED)
                | one_of("reason", [LineReason.NONE, LineReason.COST_VARIATION]),
                name="provisional_lines_resolved_reason",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0, confidence__lte=1), name="provisional_lines_confidence_range"
            ),
            models.CheckConstraint(condition=not_blank("description"), name="provisional_lines_description_not_blank"),
            models.CheckConstraint(
                condition=Q(received_quantity__isnull=True) | Q(received_quantity__gte=0),
                name="provisional_lines_received_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(retail_price__isnull=True) | Q(retail_price__gte=0),
                name="provisional_lines_price_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.position}. {self.description}"


class ConfirmedEntry(BaseModel):
    """The final record of a reviewed sheet. In the store system, a purchase."""

    sheet = models.OneToOneField(
        ReceivedSheet, on_delete=models.PROTECT, related_name="entry", verbose_name="hoja"
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="entries", verbose_name="proveedor")
    entry_date = models.DateField("fecha")
    folio = models.CharField("folio", max_length=60, blank=True)
    total = models.DecimalField("total", **MONEY, null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="confirmada por"
    )
    created_at = models.DateTimeField("creada en", auto_now_add=True)

    class Meta:
        db_table = "confirmed_entries"
        verbose_name = "entrada confirmada"
        verbose_name_plural = "entradas confirmadas"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total__isnull=True) | Q(total__gte=0), name="confirmed_entries_total_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.supplier} {self.folio} ({self.entry_date})"


class EntryLine(BaseModel):
    """An approved line. In the store system, a purchase line."""

    entry = models.ForeignKey(ConfirmedEntry, on_delete=models.PROTECT, related_name="lines", verbose_name="entrada")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="producto")
    source_line = models.OneToOneField(
        ProvisionalLine,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="entry_line",
        verbose_name="renglón de origen",
    )
    quantity = models.DecimalField("cantidad", **QUANTITY)
    unit_cost = models.DecimalField("costo unitario", **MONEY)
    retail_price = models.DecimalField("precio público", **MONEY, null=True, blank=True)

    class Meta:
        db_table = "entry_lines"
        verbose_name = "renglón de entrada"
        verbose_name_plural = "renglones de entrada"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="entry_lines_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="entry_lines_cost_not_negative"),
            models.CheckConstraint(
                condition=Q(retail_price__isnull=True) | Q(retail_price__gte=0),
                name="entry_lines_price_not_negative",
            ),
        ]

    @property
    def subtotal(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.quantity} × {self.product}"
