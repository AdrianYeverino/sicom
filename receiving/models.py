"""What the reader saw on a supplier document, what the person corrected,
and the entry created once it is confirmed.

Provisional zone: ReceivedDocument, DocumentPage, PageReading and
ProvisionalLine. Nothing in them touches the catalog.
Final zone: ConfirmedEntry and EntryLine, created only from approved lines;
in the store system they map onto a purchase and its lines.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import Product, Supplier
from core.db import MONEY, PERCENT, QUANTITY, BaseModel, TimestampedModel, not_blank, null_or_at_least, one_of
from core.history import TrackedModel


class DocumentStatus(models.TextChoices):
    READING = "reading", "Leyendo"
    IN_REVIEW = "in_review", "En revisión"
    CONFIRMED = "confirmed", "Confirmado"
    DISCARDED = "discarded", "Descartado"


class DocumentWarning(models.TextChoices):
    MISSING_PAGES = "missing_pages", "Falta una página"
    SUBTOTAL_MISMATCH = "subtotal_mismatch", "La suma de importes no coincide con el subtotal"
    TOTAL_MISMATCH = "total_mismatch", "Subtotal más IVA no coincide con el total"
    TAX_MISMATCH = "tax_mismatch", "El IVA no es el 16 % del subtotal"
    DUPLICATE_DOCUMENT = "duplicate_document", "Este folio ya se subió"
    DUPLICATE_PHOTO = "duplicate_photo", "Esta foto ya se subió"
    UNUSUAL_DATE = "unusual_date", "Fecha fuera de lo normal"
    FOREIGN_CURRENCY = "foreign_currency", "La hoja menciona otra moneda"


class ReceivedDocument(TrackedModel, TimestampedModel):
    """One supplier document (invoice, delivery note or order), of one or more pages."""

    status = models.CharField("estado", max_length=10, choices=DocumentStatus, default=DocumentStatus.READING)
    # As read from the sheet. The final values live in the confirmed entry.
    supplier_name_read = models.CharField("proveedor leído", max_length=200, blank=True)
    supplier_rfc_read = models.CharField("RFC leído", max_length=13, blank=True)
    document_type_read = models.CharField("tipo leído", max_length=60, blank=True)
    folio_read = models.CharField("folio leído", max_length=60, blank=True)
    document_date_read = models.DateField("fecha leída", null=True, blank=True)
    subtotal_read = models.DecimalField("subtotal leído", **MONEY, null=True, blank=True)
    tax_read = models.DecimalField("IVA leído", **MONEY, null=True, blank=True)
    total_read = models.DecimalField("total leído", **MONEY, null=True, blank=True)
    printed_page_count = models.PositiveSmallIntegerField("páginas impresas", null=True, blank=True)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="documents", verbose_name="proveedor"
    )
    # Only the warnings that apply, as DocumentWarning codes.
    warnings = models.JSONField("avisos", default=list, blank=True)
    # The thresholds used to verify it, to know what each test ran with.
    thresholds = models.JSONField("umbrales", default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="subido por"
    )

    tracked_fields = ("status", "supplier")

    class Meta:
        db_table = "received_documents"
        verbose_name = "documento recibido"
        verbose_name_plural = "documentos recibidos"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=one_of("status", DocumentStatus.values), name="received_documents_status_valid"
            ),
        ]

    def __str__(self):
        return f"{self.supplier or self.supplier_name_read or 'Documento'} {self.folio_read}".strip()


class ReadStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    READ = "read", "Leída"
    FAILED = "failed", "Falló"


class DocumentPage(TimestampedModel):
    """One photo. The photo stays on the device; this keeps its trace and a thumbnail."""

    document = models.ForeignKey(
        ReceivedDocument, on_delete=models.PROTECT, related_name="pages", verbose_name="documento"
    )
    upload_order = models.PositiveSmallIntegerField("orden")
    printed_page = models.PositiveSmallIntegerField("página impresa", null=True, blank=True)
    original_filename = models.CharField("archivo original", max_length=255, blank=True)
    file_sha256 = models.CharField("huella", max_length=64, db_index=True)
    captured_at = models.DateTimeField("tomada en", null=True, blank=True)
    width = models.PositiveIntegerField("ancho")
    height = models.PositiveIntegerField("alto")
    file_size = models.PositiveIntegerField("tamaño")
    thumbnail = models.BinaryField("miniatura")
    device = models.CharField("dispositivo", max_length=200, blank=True)
    read_status = models.CharField("lectura", max_length=8, choices=ReadStatus, default=ReadStatus.PENDING)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="subida por"
    )

    class Meta:
        db_table = "document_pages"
        verbose_name = "página"
        verbose_name_plural = "páginas"
        ordering = ["document", "upload_order"]
        constraints = [
            models.UniqueConstraint(fields=["document", "upload_order"], name="document_pages_order_unique"),
            models.CheckConstraint(
                condition=one_of("read_status", ReadStatus.values), name="document_pages_read_status_valid"
            ),
            models.CheckConstraint(condition=Q(file_sha256__regex=r"^[0-9a-f]{64}$"), name="document_pages_sha256_format"),
        ]

    def __str__(self):
        return f"{self.document} · {self.upload_order}"


class ReadingOutcome(models.TextChoices):
    OK = "ok", "Correcta"
    AUTH = "auth", "Llave inválida"
    NO_CREDIT = "no_credit", "Sin crédito"
    RATE_LIMIT = "rate_limit", "Demasiadas peticiones"
    PROVIDER_DOWN = "provider_down", "Servicio caído"
    TIMEOUT = "timeout", "Tiempo agotado"
    TRUNCATED = "truncated", "Respuesta cortada"
    INVALID_JSON = "invalid_json", "Respuesta ilegible"
    SCHEMA = "schema", "Respuesta fuera del formato"
    NOT_A_DOCUMENT = "not_a_document", "No es una hoja de proveedor"


class PageReading(BaseModel):
    """One call to the model. Never changes: a retry is a new row, and every
    attempt is kept because every attempt is paid for."""

    page = models.ForeignKey(DocumentPage, on_delete=models.PROTECT, related_name="readings", verbose_name="página")
    outcome = models.CharField("resultado", max_length=14, choices=ReadingOutcome)
    error_detail = models.CharField("detalle", max_length=500, blank=True)
    model = models.CharField("modelo", max_length=100)
    provider = models.CharField("proveedor del modelo", max_length=60, blank=True)
    prompt_version = models.CharField("versión del prompt", max_length=20)
    image_side = models.PositiveSmallIntegerField("lado enviado (px)")
    input_tokens = models.PositiveIntegerField("tokens de entrada", null=True, blank=True)
    output_tokens = models.PositiveIntegerField("tokens de salida", null=True, blank=True)
    cost_usd = models.DecimalField("costo USD", max_digits=10, decimal_places=6, null=True, blank=True)
    seconds = models.DecimalField("segundos", max_digits=7, decimal_places=2, null=True, blank=True)
    from_cache = models.BooleanField("lectura guardada", default=False)
    # The exact answer, to audit and to measure the reader against what was confirmed.
    raw = models.JSONField("respuesta", null=True, blank=True)
    created_at = models.DateTimeField("fecha", auto_now_add=True)

    class Meta:
        db_table = "page_readings"
        verbose_name = "lectura"
        verbose_name_plural = "lecturas"
        ordering = ["page", "created_at"]
        constraints = [
            models.CheckConstraint(condition=one_of("outcome", ReadingOutcome.values), name="page_readings_outcome_valid"),
            models.CheckConstraint(condition=null_or_at_least("cost_usd"), name="page_readings_cost_not_negative"),
        ]

    def __str__(self):
        return f"{self.page} · {self.outcome}"


class LineStatus(models.TextChoices):
    RESOLVED = "resolved", "Resuelto"
    FLAGGED = "flagged", "Separado"
    CONFIRMED = "confirmed", "Confirmado"
    DISCARDED = "discarded", "No llegó"


class LineReason(models.TextChoices):
    """Why a line was set apart. Checked in this order; the first that fails is shown."""

    NONE = "", "Ninguno"
    MISSING_VALUE = "missing_value", "Falta un dato"
    AMOUNT_MISMATCH = "amount_mismatch", "Cantidad por costo no da el importe"
    ILLEGIBLE_TEXT = "illegible_text", "Lectura dudosa"
    CODE_MISMATCH = "code_mismatch", "La clave no corresponde a la descripción"
    UNRECOGNIZED_PRODUCT = "unrecognized_product", "Producto no reconocido"
    COST_VARIATION = "cost_variation", "El costo cambió"


# Reasons that send a line to the exceptions tray. A cost variation is only a warning.
FLAG_REASONS = [
    LineReason.MISSING_VALUE,
    LineReason.AMOUNT_MISMATCH,
    LineReason.ILLEGIBLE_TEXT,
    LineReason.CODE_MISMATCH,
    LineReason.UNRECOGNIZED_PRODUCT,
]


class ProvisionalLine(TrackedModel, TimestampedModel):
    """One line as the reader saw it, plus what the person corrects while counting."""

    document = models.ForeignKey(
        ReceivedDocument, on_delete=models.PROTECT, related_name="lines", verbose_name="documento"
    )
    page = models.ForeignKey(DocumentPage, on_delete=models.PROTECT, related_name="lines", verbose_name="página")
    position = models.PositiveSmallIntegerField("renglón")
    # Read from the sheet, as printed. Quantity is empty when it cannot be seen.
    quantity = models.DecimalField("cantidad", **QUANTITY, null=True, blank=True)
    unit = models.CharField("unidad", max_length=30, blank=True)
    supplier_code = models.CharField("clave del proveedor", max_length=60, blank=True)
    description = models.CharField("descripción", max_length=300)
    unit_cost = models.DecimalField("costo unitario", **MONEY)
    # Only to check the sheet's arithmetic: the entry calculates its own subtotal.
    amount = models.DecimalField("importe", **MONEY)
    # The retail price the store sometimes writes by hand next to the cost.
    handwritten_price = models.DecimalField("precio escrito a mano", **MONEY, null=True, blank=True)
    confidence = models.DecimalField("confianza", max_digits=4, decimal_places=3)
    # The verifier's decision.
    amount_matches = models.BooleanField("cuadra", null=True, blank=True)
    status = models.CharField("estado", max_length=10, choices=LineStatus, default=LineStatus.RESOLVED)
    reason = models.CharField("motivo", max_length=24, choices=LineReason, default=LineReason.NONE, blank=True)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="producto"
    )
    # Captured by the person during review.
    received_quantity = models.DecimalField("cantidad recibida", **QUANTITY, null=True, blank=True)
    margin_percent = models.DecimalField("margen %", **PERCENT, null=True, blank=True)
    retail_price = models.DecimalField("precio público", **MONEY, null=True, blank=True)
    # Measures the goal of lines resolved without correction.
    corrected_by_person = models.BooleanField("corregido por la persona", default=False)

    tracked_fields = (
        "quantity", "unit", "supplier_code", "description", "unit_cost", "amount", "status", "product",
        "received_quantity", "margin_percent", "retail_price",
    )

    class Meta:
        db_table = "provisional_lines"
        verbose_name = "renglón provisional"
        verbose_name_plural = "renglones provisionales"
        ordering = ["document", "position"]
        constraints = [
            models.UniqueConstraint(fields=["document", "position"], name="provisional_lines_position_unique"),
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
            # Whether it adds up is unknown exactly when the quantity is.
            models.CheckConstraint(
                condition=Q(quantity__isnull=True, amount_matches__isnull=True)
                | Q(quantity__isnull=False, amount_matches__isnull=False),
                name="provisional_lines_matches_needs_quantity",
            ),
            # "Did not arrive" means nothing was received.
            models.CheckConstraint(
                condition=~Q(status=LineStatus.DISCARDED) | Q(received_quantity=0),
                name="provisional_lines_discarded_received_zero",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0, confidence__lte=1), name="provisional_lines_confidence_range"
            ),
            models.CheckConstraint(condition=not_blank("description"), name="provisional_lines_description_not_blank"),
            models.CheckConstraint(
                condition=null_or_at_least("received_quantity"), name="provisional_lines_received_not_negative"
            ),
            models.CheckConstraint(
                condition=null_or_at_least("margin_percent"), name="provisional_lines_margin_not_negative"
            ),
            models.CheckConstraint(
                condition=null_or_at_least("retail_price"), name="provisional_lines_price_not_negative"
            ),
            models.CheckConstraint(
                condition=null_or_at_least("handwritten_price"), name="provisional_lines_handwritten_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.position}. {self.description}"


class ConfirmedEntry(TimestampedModel):
    """The final record of a reviewed document. In the store system, a purchase."""

    document = models.OneToOneField(
        ReceivedDocument, on_delete=models.PROTECT, related_name="entry", verbose_name="documento"
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="entries", verbose_name="proveedor")
    folio = models.CharField("folio", max_length=60, blank=True)
    entry_date = models.DateField("fecha")
    printed_total = models.DecimalField("total impreso", **MONEY, null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="confirmada por"
    )
    confirmed_at = models.DateTimeField("confirmada en", auto_now_add=True)

    class Meta:
        db_table = "confirmed_entries"
        verbose_name = "entrada confirmada"
        verbose_name_plural = "entradas confirmadas"
        ordering = ["-confirmed_at"]
        constraints = [
            # The same supplier document can never be confirmed twice.
            models.UniqueConstraint(
                fields=["supplier", "folio"], condition=~Q(folio=""), name="confirmed_entries_folio_unique"
            ),
            models.CheckConstraint(condition=null_or_at_least("printed_total"), name="confirmed_entries_total_not_negative"),
        ]

    def __str__(self):
        return f"{self.supplier} {self.folio} ({self.entry_date})"


class EntryLine(TimestampedModel):
    """An approved line. In the store system, a purchase line."""

    entry = models.ForeignKey(ConfirmedEntry, on_delete=models.PROTECT, related_name="lines", verbose_name="entrada")
    source_line = models.OneToOneField(
        ProvisionalLine, on_delete=models.PROTECT, related_name="entry_line", verbose_name="renglón de origen"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="entry_lines", verbose_name="producto")
    quantity = models.DecimalField("cantidad", **QUANTITY)
    unit_cost = models.DecimalField("costo unitario", **MONEY)
    margin_percent = models.DecimalField("margen %", **PERCENT, null=True, blank=True)
    retail_price = models.DecimalField("precio público", **MONEY, null=True, blank=True)

    class Meta:
        db_table = "entry_lines"
        verbose_name = "renglón de entrada"
        verbose_name_plural = "renglones de entrada"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="entry_lines_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="entry_lines_cost_not_negative"),
            models.CheckConstraint(condition=null_or_at_least("margin_percent"), name="entry_lines_margin_not_negative"),
            models.CheckConstraint(condition=null_or_at_least("retail_price"), name="entry_lines_price_not_negative"),
        ]

    @property
    def subtotal(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.quantity} × {self.product}"
