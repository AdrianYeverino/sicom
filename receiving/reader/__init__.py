"""The sheet reader: a photo goes to a multimodal model through OpenRouter and
comes back as the sheet's header and lines, validated against schema.json.

schema.json and prompt.md are the ones measured in the model comparison
(2026-10-02). They stay in Spanish, as tested: the sheets are in Spanish,
and changing the contract would invalidate that measurement. The Spanish
keys are mapped to English names here, at the edge.

This module only reads. Checking the arithmetic, recognizing products and
writing to the database happen elsewhere.
"""

import base64
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import jsonschema
from django.conf import settings
from openai import OpenAI

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
PROMPT = (HERE / "prompt.md").read_text(encoding="utf-8")

OPENROUTER_URL = "https://openrouter.ai/api/v1"


class ReaderError(Exception):
    """The model's answer could not be used: the sheet has to be photographed again.

    Keeps the usage data, because a failed reading is still paid for."""

    def __init__(self, message, usage=None, raw=""):
        super().__init__(message)
        self.usage = usage
        self.raw = raw


@dataclass
class Usage:
    model: str
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: Decimal | None
    seconds: Decimal
    finish_reason: str | None


@dataclass
class ReadLine:
    quantity: Decimal
    unit: str
    supplier_code: str
    description: str
    unit_cost: Decimal
    amount: Decimal
    confidence: Decimal


@dataclass
class Reading:
    supplier_name: str
    document_type: str
    folio: str
    date: str | None
    subtotal: Decimal | None
    total: Decimal | None
    lines: list[ReadLine]
    usage: Usage
    raw: dict = field(repr=False)


def _client():
    if not settings.OPENROUTER_API_KEY:
        raise ReaderError("OPENROUTER_API_KEY is not set.")
    return OpenAI(base_url=OPENROUTER_URL, api_key=settings.OPENROUTER_API_KEY)


def _data_url(image: bytes, content_type: str) -> str:
    return f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"


def _parse_json(text: str):
    """Strips ```json fences if the model added them, then parses."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return json.loads(t)


def _decimal(value):
    # Through str, so 0.10 stays 0.10 and not 0.1000000000000000055...
    return None if value is None else Decimal(str(value))


def _usage(response, model, seconds) -> Usage:
    usage = response.usage
    output_details = getattr(usage, "completion_tokens_details", None) if usage else None
    usage_extra = (getattr(usage, "model_extra", None) or {}) if usage else {}
    response_extra = getattr(response, "model_extra", None) or {}
    return Usage(
        model=model,
        provider=response_extra.get("provider"),
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
        reasoning_tokens=getattr(output_details, "reasoning_tokens", None) if output_details else None,
        cost_usd=_decimal(usage_extra.get("cost")),
        seconds=Decimal(str(round(seconds, 2))),
        finish_reason=response.choices[0].finish_reason if response.choices else None,
    )


def read_sheet(image: bytes, content_type: str = "image/jpeg", model: str | None = None) -> Reading:
    """Reads one sheet. Each call is a new single-message conversation: the
    model never sees an earlier reading. The photo is sent full size."""
    model = model or settings.READER_MODEL
    start = time.perf_counter()
    response = _client().chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=settings.READER_MAX_TOKENS,
        messages=[
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(image, content_type)}},
                    {"type": "text", "text": "Lee esta hoja y devuelve el JSON con el esquema."},
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "hoja_proveedor", "strict": True, "schema": SCHEMA},
        },
        extra_body={
            # Each answer reports its tokens and the real cost in USD.
            "usage": {"include": True},
            # Only route to providers that honor the schema.
            "provider": {"require_parameters": True},
        },
    )
    usage = _usage(response, model, time.perf_counter() - start)
    content = response.choices[0].message.content or ""

    # Validated always, even when the provider promises a strict schema.
    try:
        data = _parse_json(content)
        jsonschema.validate(data, SCHEMA)
    except json.JSONDecodeError as e:
        raise ReaderError(f"Invalid JSON: {e}", usage, content) from e
    except jsonschema.ValidationError as e:
        raise ReaderError(f"Does not match the schema: {e.message}", usage, content) from e

    return Reading(
        supplier_name=data["proveedor"] or "",
        document_type=data["tipo_documento"] or "",
        folio=data["folio"] or "",
        date=data["fecha"],
        subtotal=_decimal(data["subtotal"]),
        total=_decimal(data["total"]),
        lines=[
            ReadLine(
                quantity=_decimal(r["cantidad"]),
                unit=r["unidad"] or "",
                supplier_code=r["clave"] or "",
                description=r["descripcion"],
                unit_cost=_decimal(r["costo_unitario"]),
                amount=_decimal(r["importe"]),
                confidence=_decimal(r["confianza"]),
            )
            for r in data["renglones"]
        ],
        usage=usage,
        raw=data,
    )
