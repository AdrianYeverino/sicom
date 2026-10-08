"""Retail price and margin. Supplier costs come before tax; the store sells
with tax included, so:

    retail price = cost × (1 + tax) × (1 + margin / 100)

The margin is a markup over the cost with tax. Prices are never rounded on
their own: rounding is the person's decision, the screen only offers it.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

CENT = Decimal("0.01")


def _q(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def price_for(cost, margin_percent, tax_rate=None):
    tax_rate = settings.TAX_RATE if tax_rate is None else tax_rate
    return _q(cost * (1 + tax_rate) * (1 + margin_percent / 100))


def margin_for(cost, price, tax_rate=None):
    """The margin a price implies. None when there is no cost to compare."""
    tax_rate = settings.TAX_RATE if tax_rate is None else tax_rate
    if not cost:
        return None
    return _q((price / (cost * (1 + tax_rate)) - 1) * 100)


@dataclass
class PriceOption:
    key: str  # handwritten, keep_margin, keep_price, default
    label: str
    margin_percent: Decimal | None
    retail_price: Decimal


def options(cost, product=None, handwritten_price=None):
    """Every reasonable price for a line, best first. When a known product
    arrives with a new cost, both "keep the margin" and "keep the price"
    are offered, so the person decides with both numbers in view."""
    found = []
    if handwritten_price:
        found.append(PriceOption("handwritten", "Escrito a mano", margin_for(cost, handwritten_price), handwritten_price))
    if product is not None and product.margin_percent is not None:
        found.append(PriceOption("keep_margin", "Mismo margen", product.margin_percent, price_for(cost, product.margin_percent)))
    if product is not None and product.retail_price is not None:
        found.append(PriceOption("keep_price", "Mismo precio", margin_for(cost, product.retail_price), product.retail_price))
    if product is None or product.margin_percent is None:
        default = settings.DEFAULT_MARGIN_PERCENT
        found.append(PriceOption("default", "Margen general", default, price_for(cost, default)))
    return found
