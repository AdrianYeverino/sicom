"""The catalog the sheet lines are matched against.

Kept minimal on purpose: the store system already has a full catalog, and
these tables map one to one onto its suppliers, products, supplier codes
and aliases.
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.db import MONEY, BaseModel, not_blank, one_of


class Supplier(BaseModel):
    name = models.CharField("nombre", max_length=120)
    active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("creado en", auto_now_add=True)

    class Meta:
        db_table = "suppliers"
        verbose_name = "proveedor"
        verbose_name_plural = "proveedores"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="suppliers_name_unique"),
            models.CheckConstraint(condition=not_blank("name"), name="suppliers_name_not_blank"),
        ]

    def __str__(self):
        return self.name


class CostSource(models.TextChoices):
    SUPPLIER = "supplier", "De proveedor"
    ESTIMATED = "estimated", "Estimado"
    UNKNOWN = "unknown", "Desconocido"


class Product(BaseModel):
    name = models.CharField("nombre", max_length=200)
    unit = models.CharField("unidad", max_length=30, blank=True)
    last_cost = models.DecimalField("último costo", **MONEY, null=True, blank=True)
    cost_source = models.CharField(
        "origen del costo", max_length=12, choices=CostSource, default=CostSource.UNKNOWN
    )
    active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("creado en", auto_now_add=True)

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
            models.CheckConstraint(
                condition=Q(last_cost__isnull=True) | Q(last_cost__gte=0), name="products_cost_not_negative"
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


class ProductSupplierCode(BaseModel):
    """How a supplier knows a product. The first criterion to recognize a line."""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="supplier_codes", verbose_name="producto"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="product_codes", verbose_name="proveedor"
    )
    code = models.CharField("clave", max_length=60)
    supplier_description = models.CharField("descripción del proveedor", max_length=200, blank=True)
    created_at = models.DateTimeField("creado en", auto_now_add=True)

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


class ProductAlias(BaseModel):
    """Another name for the product, matched against the line description."""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="aliases", verbose_name="producto"
    )
    alias = models.CharField("nombre alterno", max_length=200)
    created_at = models.DateTimeField("creado en", auto_now_add=True)

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
