from django.contrib import admin

from .models import Product, ProductAlias, ProductSupplierCode, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "active"]
    search_fields = ["name"]


class ProductSupplierCodeInline(admin.TabularInline):
    model = ProductSupplierCode
    extra = 0


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "last_cost", "cost_source", "active"]
    list_filter = ["cost_source", "active"]
    search_fields = ["name", "aliases__alias", "supplier_codes__code"]
    inlines = [ProductSupplierCodeInline, ProductAliasInline]
