"""The page reader: one photo goes to a multimodal model through OpenRouter
and comes back as that page's header and lines, validated against schema.json.

schema.json and prompt.md are in Spanish because the sheets are: the model
reads Spanish documents and answers in their terms. Version 2 extends the
contract measured in the model comparison (2026-10-02) with page numbers,
the supplier's tax ID, the price written by hand and nullable quantities.
The Spanish keys are mapped to English names here, at the edge.

This module only reads one page, once. It never raises for an API failure:
it returns the outcome, so every attempt can be recorded with its cost.
Retries, merging pages and checking the numbers happen elsewhere.
"""

import base64
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

import jsonschema
import openai
from django.conf import settings

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
PROMPT = (HERE / "prompt.md").read_text(encoding="utf-8")
PROMPT_VERSION = "v3"

OPENROUTER_URL = "https://openrouter.ai/api/v1"
TIMEOUT_SECONDS = 90

logger = logging.getLogger(__name__)

# Outcomes. Same codes as receiving.models.ReadingOutcome.
OK = "ok"
AUTH = "auth"
NO_CREDIT = "no_credit"
RATE_LIMIT = "rate_limit"
PROVIDER_DOWN = "provider_down"
TIMEOUT = "timeout"
TRUNCATED = "truncated"
INVALID_JSON = "invalid_json"
SCHEMA_ERROR = "schema"
NOT_A_DOCUMENT = "not_a_document"

# Worth trying again after a pause; the others will fail the same way.
RETRYABLE = {RATE_LIMIT, PROVIDER_DOWN, TIMEOUT}


@dataclass
class Usage:
    model: str
    provider: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: Decimal | None = None
    seconds: Decimal | None = None


@dataclass
class ReadLine:
    quantity: Decimal | None
    unit: str
    supplier_code: str
    description: str
    unit_cost: Decimal
    amount: Decimal
    handwritten_price: Decimal | None
    confidence: Decimal
    discount_percent: Decimal | None = None


@dataclass
class PageContent:
    page_number: int | None
    page_count: int | None
    supplier_name: str
    supplier_rfc: str
    document_type: str
    folio: str
    date: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    lines: list[ReadLine]


@dataclass
class PageResult:
    outcome: str
    usage: Usage
    prompt_version: str = PROMPT_VERSION
    content: PageContent | None = None
    raw: dict | None = field(default=None, repr=False)
    error_detail: str = ""
    from_cache: bool = False

    @property
    def ok(self):
        return self.outcome == OK


def _decimal(value):
    # Through str, so 0.10 stays 0.10 and not 0.1000000000000000055...
    return None if value is None else Decimal(str(value))


def _text(value):
    return (value or "").strip()


def parse_json(text: str):
    """Strips ```json fences if the model added them, then parses."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return json.loads(t)


def to_content(data: dict) -> PageContent:
    """The validated Spanish answer, with English names and exact decimals."""
    return PageContent(
        page_number=data["pagina"],
        page_count=data["paginas_totales"],
        supplier_name=_text(data["proveedor"]),
        supplier_rfc=_text(data["rfc_proveedor"]).upper().replace(" ", "").replace("-", ""),
        document_type=_text(data["tipo_documento"]),
        folio=_text(data["folio"]),
        date=data["fecha"],
        subtotal=_decimal(data["subtotal"]),
        tax=_decimal(data["iva"]),
        total=_decimal(data["total"]),
        lines=[
            ReadLine(
                quantity=_decimal(r["cantidad"]),
                unit=_text(r["unidad"]),
                supplier_code=_text(r["clave"]),
                description=_text(r["descripcion"]),
                unit_cost=_decimal(r["costo_unitario"]),
                amount=_decimal(r["importe"]),
                handwritten_price=_decimal(r["precio_escrito"]),
                confidence=_decimal(r["confianza"]),
                discount_percent=_decimal(r["descuento"]) or None,
            )
            for r in data["renglones"]
        ],
    )


def evaluate(text: str, finish_reason: str | None):
    """Turns the model's text into (outcome, raw, content, detail)."""
    if finish_reason == "length":
        return TRUNCATED, None, None, "The answer reached the token limit."
    try:
        data = parse_json(text)
    except json.JSONDecodeError as e:
        return INVALID_JSON, None, None, str(e)[:500]
    # Validated always, even when the provider promises a strict schema.
    try:
        jsonschema.validate(data, SCHEMA)
    except jsonschema.ValidationError as e:
        return SCHEMA_ERROR, data, None, e.message[:500]
    if not data["es_hoja"]:
        return NOT_A_DOCUMENT, data, None, ""
    return OK, data, to_content(data), ""


# --- Saved readings -----------------------------------------------------------


def cache_key(image: bytes, model: str) -> str:
    return hashlib.sha256(image).hexdigest() + "-" + model.replace("/", "_") + "-" + PROMPT_VERSION


def _cache_path(key):
    return Path(settings.READER_CACHE_DIR) / f"{key}.json" if settings.READER_CACHE_DIR else None


def _from_cache(key, model):
    path = _cache_path(key)
    if not path or not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    usage = Usage(model=model, **{k: v for k, v in saved["usage"].items() if k != "model"})
    usage.cost_usd = _decimal(usage.cost_usd)
    usage.seconds = _decimal(usage.seconds)
    outcome, raw, content, detail = OK, saved["raw"], to_content(saved["raw"]), ""
    return PageResult(outcome, usage, content=content, raw=raw, error_detail=detail, from_cache=True)


def _save_cache(key, result):
    path = _cache_path(key)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    usage = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(result.usage).items()}
    path.write_text(json.dumps({"raw": result.raw, "usage": usage}, ensure_ascii=False, indent=1), encoding="utf-8")


# --- The call -----------------------------------------------------------------


def _client():
    return openai.OpenAI(
        base_url=OPENROUTER_URL, api_key=settings.OPENROUTER_API_KEY, timeout=TIMEOUT_SECONDS, max_retries=0
    )


def _provider_preferences():
    preferences = {"require_parameters": True}
    if settings.READER_PROVIDER:
        preferences.update(order=[settings.READER_PROVIDER], allow_fallbacks=False)
    return preferences


def _usage_from(response, model, seconds):
    usage = response.usage
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    usage_extra = (getattr(usage, "model_extra", None) or {}) if usage else {}
    response_extra = getattr(response, "model_extra", None) or {}
    return Usage(
        model=model,
        provider=response_extra.get("provider") or "",
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
        reasoning_tokens=getattr(details, "reasoning_tokens", None) if details else None,
        cost_usd=_decimal(usage_extra.get("cost")),
        seconds=Decimal(str(round(seconds, 2))),
    )


def _classify(error):
    if isinstance(error, openai.APITimeoutError):
        return TIMEOUT
    if isinstance(error, openai.APIConnectionError):
        return PROVIDER_DOWN
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AUTH
    if isinstance(error, openai.RateLimitError):
        return RATE_LIMIT
    if isinstance(error, openai.APIStatusError):
        if error.status_code == 402:
            return NO_CREDIT
        if error.status_code >= 500 or error.status_code in (404, 408):
            # 404: no provider currently serves this model with these settings.
            return PROVIDER_DOWN
    return PROVIDER_DOWN


def read_page(image_jpeg: bytes, model: str | None = None) -> PageResult:
    """Reads one page once. Each call is a new single-message conversation:
    the model never sees another page or an earlier reading."""
    model = model or settings.READER_MODEL
    key = cache_key(image_jpeg, model)
    cached = _from_cache(key, model)
    if cached:
        return cached

    if not settings.OPENROUTER_API_KEY:
        return PageResult(AUTH, Usage(model=model), error_detail="OPENROUTER_API_KEY is not set.")

    data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
    start = time.perf_counter()
    try:
        response = _client().chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=settings.READER_MAX_TOKENS,
            messages=[
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": "Lee esta página y devuelve el JSON con el esquema."},
                    ],
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "pagina_proveedor", "strict": True, "schema": SCHEMA},
            },
            extra_body={
                # Each answer reports its tokens and the real cost in USD.
                "usage": {"include": True},
                "provider": _provider_preferences(),
            },
        )
    except openai.OpenAIError as error:
        seconds = Decimal(str(round(time.perf_counter() - start, 2)))
        logger.warning("reader: %s", error)
        return PageResult(_classify(error), Usage(model=model, seconds=seconds), error_detail=str(error)[:500])

    usage = _usage_from(response, model, time.perf_counter() - start)
    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "") if choice else ""
    outcome, raw, content, detail = evaluate(text, choice.finish_reason if choice else None)
    result = PageResult(outcome, usage, content=content, raw=raw, error_detail=detail)
    if result.ok:
        _save_cache(key, result)
    return result
