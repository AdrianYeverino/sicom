from django.contrib import admin

from .models import ChangeLog


@admin.register(ChangeLog)
class ChangeLogAdmin(admin.ModelAdmin):
    list_display = ["changed_at", "table_name", "row_id", "action", "source", "changed_by"]
    list_filter = ["table_name", "action", "source"]
    search_fields = ["row_id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
