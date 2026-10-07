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


def one_of(field, values):
    """CHECK that the field only takes the values of its choices."""
    return Q(**{f"{field}__in": list(values)})


def not_blank(field):
    """CHECK that a text has at least one non-space character."""
    return Q(**{f"{field}__regex": r"\S"})


# Money with cents and quantities with three decimals (meters, kilos).
MONEY = {"max_digits": 12, "decimal_places": 2}
QUANTITY = {"max_digits": 12, "decimal_places": 3}
