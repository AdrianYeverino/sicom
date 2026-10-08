from django.contrib import admin

from .models import ConfirmedEntry, DocumentPage, EntryLine, PageReading, ProvisionalLine, ReceivedDocument


class DocumentPageInline(admin.TabularInline):
    model = DocumentPage
    extra = 0
    fields = ["upload_order", "printed_page", "original_filename", "captured_at", "read_status", "uploaded_by"]
    readonly_fields = fields
    can_delete = False


class ProvisionalLineInline(admin.TabularInline):
    model = ProvisionalLine
    extra = 0
    fields = [
        "position", "quantity", "unit", "supplier_code", "description", "unit_cost", "amount",
        "confidence", "amount_matches", "status", "reason", "product",
    ]
    can_delete = False


@admin.register(ReceivedDocument)
class ReceivedDocumentAdmin(admin.ModelAdmin):
    list_display = ["__str__", "document_date_read", "status", "created_by", "created_at"]
    list_filter = ["status", "supplier"]
    readonly_fields = ["warnings", "thresholds", "created_by"]
    inlines = [DocumentPageInline, ProvisionalLineInline]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PageReading)
class PageReadingAdmin(admin.ModelAdmin):
    list_display = ["page", "outcome", "model", "provider", "input_tokens", "output_tokens", "cost_usd", "from_cache", "created_at"]
    list_filter = ["outcome", "model", "provider", "from_cache"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EntryLineInline(admin.TabularInline):
    model = EntryLine
    extra = 0
    can_delete = False


@admin.register(ConfirmedEntry)
class ConfirmedEntryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "supplier", "entry_date", "printed_total", "confirmed_by"]
    list_filter = ["supplier"]
    inlines = [EntryLineInline]

    def has_delete_permission(self, request, obj=None):
        return False
