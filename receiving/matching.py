"""Recognizing products and suppliers. No model is involved: exact keys
first, then edit distance over normalized text.

Text is normalized to upper case, without accents, punctuation or double
spaces, so "Mang. flexible 5/16" and "MANG FLEXIBLE 5/16" compare equal.
"""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from catalog.models import Product, ProductAlias, ProductSupplierCode, Supplier


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = re.sub(r"[^A-Z0-9/ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a, b):
    """0 to 1: 1 - normalized edit distance."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return Levenshtein.normalized_similarity(a, b)


def find_supplier(name, rfc):
    """By RFC first: it is exact and printed on every sheet. Then by name."""
    if rfc:
        found = Supplier.objects.filter(rfc=rfc, active=True).first()
        if found:
            return found
    wanted = normalize(name)
    if not wanted:
        return None
    best, best_score = None, 0.0
    for supplier in Supplier.objects.filter(active=True):
        score = similarity(supplier.name, name)
        if score > best_score:
            best, best_score = supplier, score
    return best if best_score >= 0.9 else None


@dataclass
class Recognition:
    product: Product | None = None
    how: str = ""  # code, description
    score: float = 0.0
    suggestion: Product | None = None  # a likely product that did not reach the threshold
    code_mismatch: bool = False


class Catalog:
    """What one document is matched against, loaded once."""

    def __init__(self, supplier):
        self.supplier = supplier
        self.codes = {}
        self.texts = []  # (normalized text, product)
        if supplier:
            for psc in ProductSupplierCode.objects.filter(supplier=supplier).select_related("product"):
                self.codes[psc.code.upper()] = psc
                if psc.supplier_description:
                    self.texts.append((psc.supplier_description, psc.product))
        for alias in ProductAlias.objects.select_related("product").filter(product__active=True):
            self.texts.append((alias.alias, alias.product))
        for product in Product.objects.filter(active=True):
            self.texts.append((product.name, product))

    def _best_text(self, description):
        best, best_score = None, 0.0
        for text, product in self.texts:
            score = similarity(text, description)
            if score > best_score:
                best, best_score = product, score
        return best, best_score

    def recognize(self, code, description, thresholds):
        match = float(thresholds["match_similarity"])
        suggest = float(thresholds["suggest_similarity"])

        known = self.codes.get((code or "").upper()) if code else None
        if known:
            # The code exists, but if the description has nothing to do with
            # what this supplier prints for it, the code was probably misread.
            if known.supplier_description and similarity(known.supplier_description, description) < suggest:
                return Recognition(suggestion=known.product, code_mismatch=True)
            return Recognition(product=known.product, how="code", score=1.0)

        product, score = self._best_text(description)
        if product and score >= match:
            # Recognized by description while the printed code is unknown,
            # and this supplier already uses another code for it: misread code.
            misread = bool(code) and any(psc.product_id == product.id for psc in self.codes.values())
            if misread:
                return Recognition(suggestion=product, score=score, code_mismatch=True)
            return Recognition(product=product, how="description", score=score)
        if product and score >= suggest:
            return Recognition(suggestion=product, score=score)
        return Recognition(score=score)
