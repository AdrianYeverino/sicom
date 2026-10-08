"""Shared model helpers. Same conventions as the store system, so the
receiving app can move there later without rewriting its constraints."""

import uuid

from django.db import models
from django.db.models import Q


class BaseModel(models.Model):
    """UUID key: the identifier does not reveal how many records the business has."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(BaseModel):
    """For every table that can change. updated_at is indexed: "everything that
    changed since" is the query a future sync needs. Nothing is ever deleted
    (rows are deactivated or discarded), so no deletion log is needed."""

    created_at = models.DateTimeField("creado en", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado en", auto_now=True, db_index=True)

    class Meta:
        abstract = True


def one_of(field, values):
    """CHECK that the field only takes the values of its choices."""
    return Q(**{f"{field}__in": list(values)})


def not_blank(field):
    """CHECK that a text has at least one non-space character."""
    return Q(**{f"{field}__regex": r"\S"})


def null_or_at_least(field, minimum=0):
    return Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__gte": minimum})


# Money with cents, quantities with three decimals (meters, kilos), percentages.
MONEY = {"max_digits": 12, "decimal_places": 2}
QUANTITY = {"max_digits": 12, "decimal_places": 3}
PERCENT = {"max_digits": 5, "decimal_places": 2}
