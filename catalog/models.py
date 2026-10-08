"""The catalog the sheet lines are matched against.

Kept minimal on purpose: the store system already has a full catalog, and
these tables map one to one onto its suppliers, products, supplier codes
and aliases. It starts empty and grows with every confirmed delivery.
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.db import MONEY, PERCENT, TimestampedModel, not_blank, null_or_at_least, one_of
from core.history import TrackedModel

# Mexican RFC: 3 letters (companies) or 4 (people), birth or founding date, 3 characters.
RFC_PATTERN = r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$"


class Supplier(TrackedModel, TimestampedModel):
    name = models.CharField("nombre", max_length=120)
    # Printed on every sheet: recognizes the supplier however its name is written.
    rfc = models.CharField("RFC", max_length=13, blank=True)
    active = models.BooleanField("activo", default=True)

    tracked_fields = ("name", "rfc", "active")

    class Meta:
        db_table = "suppliers"
        verbose_name = "proveedor"
        verbose_name_plural = "proveedores"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="suppliers_name_unique"),
            models.UniqueConstraint(fields=["rfc"], condition=~Q(rfc=""), name="suppliers_rfc_unique"),
            models.CheckConstraint(condition=not_blank("name"), name="suppliers_name_not_blank"),
            models.CheckConstraint(
                condition=Q(rfc="") | Q(rfc__regex=RFC_PATTERN), name="suppliers_rfc_format"
            ),
        ]

    def __str__(self):
        return self.name


class CostSource(models.TextChoices):
    SUPPLIER = "supplier", "De proveedor"
    ESTIMATED = "estimated", "Estimado"
    UNKNOWN = "unknown", "Desconocido"


class Product(TrackedModel, TimestampedModel):
    name = models.CharField("nombre", max_length=200)
    unit = models.CharField("unidad", max_length=30, blank=True)
    last_cost = models.DecimalField("último costo", **MONEY, null=True, blank=True)
    cost_source = models.CharField(
        "origen del costo", max_length=12, choices=CostSource, default=CostSource.UNKNOWN
    )
    # Each product has its own margin; empty means the store default applies.
    margin_percent = models.DecimalField("margen %", **PERCENT, null=True, blank=True)
    # Retail price with tax, as the store last set it.
    retail_price = models.DecimalField("precio público", **MONEY, null=True, blank=True)
    active = models.BooleanField("activo", default=True)

    tracked_fields = ("name", "unit", "last_cost", "cost_source", "margin_percent", "retail_price", "active")

    class Meta:
        db_table = "products"
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=not_blank("name"), name="products_name_not_blank"),
            models.CheckConstraint(
                condition=one_of("cost_source", CostSource.values), name="products_cost_source_valid"
            ),
            models.CheckConstraint(condition=null_or_at_least("last_cost"), name="products_cost_not_negative"),
            models.CheckConstraint(
                condition=null_or_at_least("margin_percent"), name="products_margin_not_negative"
            ),
            models.CheckConstraint(
                condition=null_or_at_least("retail_price"), name="products_price_not_negative"
            ),
            # Every cost has a source, and an empty cost is "unknown", never anything else.
            models.CheckConstraint(
                condition=Q(last_cost__isnull=True, cost_source=CostSource.UNKNOWN)
                | (Q(last_cost__isnull=False) & ~Q(cost_source=CostSource.UNKNOWN)),
                name="products_cost_and_source_consistent",
            ),
        ]

    def __str__(self):
        return self.name


class ProductSupplierCode(TrackedModel, TimestampedModel):
    """How a supplier knows a product. The first criterion to recognize a line."""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="supplier_codes", verbose_name="producto"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="product_codes", verbose_name="proveedor"
    )
    code = models.CharField("clave", max_length=60)
    # As this supplier prints it: also matched by similarity.
    supplier_description = models.CharField("descripción del proveedor", max_length=300, blank=True)

    tracked_fields = ("product", "supplier", "code", "supplier_description")

    class Meta:
        db_table = "product_supplier_codes"
        verbose_name = "clave de proveedor"
        verbose_name_plural = "claves de proveedor"
        constraints = [
            models.UniqueConstraint(fields=["supplier", "code"], name="product_supplier_codes_unique"),
            models.CheckConstraint(condition=not_blank("code"), name="product_supplier_codes_code_not_blank"),
        ]

    def __str__(self):
        return f"{self.supplier}: {self.code}"


class ProductAlias(TrackedModel, TimestampedModel):
    """Another name for the product, for suppliers that print no code."""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="aliases", verbose_name="producto"
    )
    alias = models.CharField("nombre alterno", max_length=300)

    tracked_fields = ("product", "alias")

    class Meta:
        db_table = "product_aliases"
        verbose_name = "nombre alterno"
        verbose_name_plural = "nombres alternos"
        constraints = [
            models.UniqueConstraint("product", Lower("alias"), name="product_aliases_unique"),
            models.CheckConstraint(condition=not_blank("alias"), name="product_aliases_not_blank"),
        ]

    def __str__(self):
        return self.alias
