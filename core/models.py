from django.conf import settings
from django.db import models

from .db import one_of
from .history import Source


class Action(models.TextChoices):
    CREATE = "create", "Alta"
    UPDATE = "update", "Cambio"


class ChangeLog(models.Model):
    """One row per save of a tracked row: only the fields that changed."""

    Action = Action

    table_name = models.CharField("tabla", max_length=60)
    row_id = models.UUIDField("registro")
    action = models.CharField("acción", max_length=6, choices=Action)
    # {"field": [old, new]}
    changes = models.JSONField("cambios")
    source = models.CharField("origen", max_length=10, choices=Source)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="hecho por",
    )
    changed_at = models.DateTimeField("fecha", auto_now_add=True)

    class Meta:
        db_table = "change_log"
        verbose_name = "cambio"
        verbose_name_plural = "historial de cambios"
        ordering = ["-changed_at"]
        indexes = [models.Index(fields=["table_name", "row_id", "changed_at"], name="change_log_row_idx")]
        constraints = [
            models.CheckConstraint(condition=one_of("action", Action.values), name="change_log_action_valid"),
            models.CheckConstraint(condition=one_of("source", Source.values), name="change_log_source_valid"),
        ]

    def __str__(self):
        return f"{self.table_name} {self.row_id} {self.action}"
