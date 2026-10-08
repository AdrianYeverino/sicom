from django.contrib import admin

from .models import Product, ProductAlias, ProductSupplierCode, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "rfc", "active"]
    search_fields = ["name", "rfc"]

    def has_delete_permission(self, request, obj=None):
        return False


class ProductSupplierCodeInline(admin.TabularInline):
    model = ProductSupplierCode
    extra = 0
    can_delete = False


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 0
    can_delete = False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "last_cost", "margin_percent", "retail_price", "active"]
    list_filter = ["cost_source", "active"]
    search_fields = ["name", "aliases__alias", "supplier_codes__code"]
    inlines = [ProductSupplierCodeInline, ProductAliasInline]

    def has_delete_permission(self, request, obj=None):
        return False
