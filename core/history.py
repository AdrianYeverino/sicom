"""Change history: who changed what, when, and from where.

A TrackedModel remembers its tracked fields when it is loaded and, on save,
writes one ChangeLog row with only the fields that changed (old and new
value). Who and from where come from a context set per request by
HistoryMiddleware, or explicitly with changes_by().

Rule for the code: tracked tables are changed with save(), never with
queryset.update(), which would skip the history.
"""

import contextvars
import datetime
import decimal
import uuid
from contextlib import contextmanager

from django.db import models


class Source(models.TextChoices):
    # What the reader writes is already recorded in page_readings: not logged.
    READER = "reader", "Lectura"
    REVIEW = "review", "Revisión"
    CONFIRM = "confirm", "Confirmación"
    ADMIN = "admin", "Administración"
    APP = "app", "Aplicación"


_context = contextvars.ContextVar("history_context", default=(None, Source.APP))


@contextmanager
def changes_by(user, source):
    token = _context.set((user, source))
    try:
        yield
    finally:
        _context.reset(token)


def current_context():
    return _context.get()


class HistoryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        source = Source.ADMIN if request.path.startswith("/admin/") else Source.APP
        with changes_by(user, source):
            return self.get_response(request)


def _plain(value):
    """A value as JSON can hold it."""
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


class TrackedModel(models.Model):
    """Subclasses list their tracked fields by name; foreign keys by field name."""

    tracked_fields: tuple = ()

    class Meta:
        abstract = True

    def _attnames(self):
        return {name: self._meta.get_field(name).attname for name in self.tracked_fields}

    def _snapshot(self):
        loaded = self.get_deferred_fields()
        return {
            name: _plain(getattr(self, attname))
            for name, attname in self._attnames().items()
            if attname not in loaded
        }

    @classmethod
    def from_db(cls, *args, **kwargs):
        instance = super().from_db(*args, **kwargs)
        instance._history_loaded = instance._snapshot()
        return instance

    def save(self, *args, **kwargs):
        creating = self._state.adding
        before = {} if creating else getattr(self, "_history_loaded", None)
        super().save(*args, **kwargs)
        after = self._snapshot()
        self._history_loaded = after

        user, source = current_context()
        if source == Source.READER or before is None:
            return
        if creating:
            changes = {k: [None, v] for k, v in after.items() if v not in (None, "")}
        else:
            changes = {k: [before[k], v] for k, v in after.items() if k in before and before[k] != v}
        if not changes:
            return

        from .models import ChangeLog

        ChangeLog.objects.create(
            table_name=self._meta.db_table,
            row_id=self.pk,
            action=ChangeLog.Action.CREATE if creating else ChangeLog.Action.UPDATE,
            changes=changes,
            source=source,
            changed_by=user,
        )
