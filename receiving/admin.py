from django.contrib import admin

from .models import ConfirmedEntry, EntryLine, ProvisionalLine, ReceivedSheet


class ProvisionalLineInline(admin.TabularInline):
    model = ProvisionalLine
    extra = 0
    fields = [
        "position", "quantity", "unit", "supplier_code", "description", "unit_cost", "amount",
        "confidence", "amount_matches", "status", "reason", "product",
    ]


@admin.register(ReceivedSheet)
class ReceivedSheetAdmin(admin.ModelAdmin):
    list_display = ["__str__", "sheet_date", "status", "model_used", "cost_usd", "created_at"]
    list_filter = ["status", "supplier"]
    readonly_fields = ["model_used", "input_tokens", "output_tokens", "cost_usd", "seconds", "raw_reading"]
    inlines = [ProvisionalLineInline]


class EntryLineInline(admin.TabularInline):
    model = EntryLine
    extra = 0


@admin.register(ConfirmedEntry)
class ConfirmedEntryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "supplier", "entry_date", "total", "confirmed_by"]
    list_filter = ["supplier"]
    inlines = [EntryLineInline]
