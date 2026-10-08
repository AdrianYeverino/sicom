"""The reader without calling OpenRouter: the client is replaced. Invented data."""

import json
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import httpx2
import openai
from django.test import SimpleTestCase, override_settings

from receiving import reader

PAGE = {
    "es_hoja": True,
    "pagina": 1,
    "paginas_totales": 2,
    "proveedor": "ACME TOOLS",
    "rfc_proveedor": "acm-010101-ab1",
    "tipo_documento": "Remision",
    "folio": "R-0001",
    "fecha": "2026-01-15",
    "subtotal": None,
    "iva": None,
    "total": None,
    "renglones": [
        {
            "cantidad": 2,
            "unidad": None,
            "clave": "AT-1001",
            "descripcion": "GARDEN HOSE 1/2",
            "costo_unitario": 40.5,
            "importe": 81.0,
            "descuento": None,
            "precio_escrito": 75,
            "confianza": 0.95,
        },
        {
            "cantidad": None,
            "unidad": "PZA",
            "clave": None,
            "descripcion": "WOOD SCREW 1IN",
            "costo_unitario": 0.1,
            "importe": 10.0,
            "descuento": None,
            "precio_escrito": None,
            "confianza": 0.6,
        },
    ],
}


def response(content, finish_reason="stop"):
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=900,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        model_extra={"cost": 0.0015},
    )
    choice = SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage, model_extra={"provider": "provider-x"})


def status_error(cls, status):
    request = httpx2.Request("POST", "https://example.invalid")
    return cls("failed", response=httpx2.Response(status, request=request), body=None)


@override_settings(OPENROUTER_API_KEY="test-key", READER_CACHE_DIR="", READER_PROVIDER="provider-x")
class ReadPageTest(SimpleTestCase):
    def read(self, content=None, error=None, finish_reason="stop"):
        client = mock.Mock()
        if error:
            client.chat.completions.create.side_effect = error
        else:
            client.chat.completions.create.return_value = response(content, finish_reason)
        with mock.patch.object(reader.openai, "OpenAI", return_value=client):
            result = reader.read_page(b"jpeg bytes")
        self.client_mock = client
        return result

    def test_maps_the_page_to_english_names_with_exact_decimals(self):
        result = self.read(json.dumps(PAGE))
        self.assertTrue(result.ok)
        page = result.content
        self.assertEqual((page.page_number, page.page_count), (1, 2))
        self.assertEqual(page.supplier_rfc, "ACM010101AB1")
        self.assertIsNone(page.total)
        first, second = page.lines
        self.assertEqual(first.unit, "")
        self.assertEqual(first.unit_cost, Decimal("40.5"))
        self.assertEqual(first.handwritten_price, Decimal("75"))
        self.assertIsNone(second.quantity)
        self.assertEqual(second.unit_cost, Decimal("0.1"))
        self.assertEqual(result.usage.cost_usd, Decimal("0.0015"))
        self.assertEqual(result.usage.provider, "provider-x")
        self.assertEqual(result.prompt_version, reader.PROMPT_VERSION)

    def test_asks_for_the_measured_provider_only(self):
        self.read(json.dumps(PAGE))
        sent = self.client_mock.chat.completions.create.call_args.kwargs["extra_body"]["provider"]
        self.assertEqual(sent, {"require_parameters": True, "order": ["provider-x"], "allow_fallbacks": False})

    def test_strips_json_fences(self):
        self.assertTrue(self.read("```json\n" + json.dumps(PAGE) + "\n```").ok)

    def test_an_answer_cut_by_the_token_limit_is_never_a_reading(self):
        result = self.read(json.dumps(PAGE)[:50], finish_reason="length")
        self.assertEqual(result.outcome, reader.TRUNCATED)
        self.assertEqual(result.usage.output_tokens, 900)

    def test_invalid_json(self):
        self.assertEqual(self.read("not json").outcome, reader.INVALID_JSON)

    def test_an_answer_outside_the_schema_keeps_its_raw_answer(self):
        result = self.read(json.dumps({**PAGE, "renglones": [{"cantidad": 2}]}))
        self.assertEqual(result.outcome, reader.SCHEMA_ERROR)
        self.assertIsNotNone(result.raw)

    def test_a_photo_that_is_not_a_sheet(self):
        result = self.read(json.dumps({**PAGE, "es_hoja": False, "renglones": []}))
        self.assertEqual(result.outcome, reader.NOT_A_DOCUMENT)

    def test_api_failures_are_classified_not_raised(self):
        request = httpx2.Request("POST", "https://example.invalid")
        cases = [
            (status_error(openai.AuthenticationError, 401), reader.AUTH),
            (status_error(openai.APIStatusError, 402), reader.NO_CREDIT),
            (status_error(openai.RateLimitError, 429), reader.RATE_LIMIT),
            (status_error(openai.InternalServerError, 503), reader.PROVIDER_DOWN),
            (openai.APITimeoutError(request=request), reader.TIMEOUT),
        ]
        for error, outcome in cases:
            with self.subTest(outcome=outcome):
                self.assertEqual(self.read(error=error).outcome, outcome)

    @override_settings(OPENROUTER_API_KEY="")
    def test_without_key_it_says_so_without_calling(self):
        self.assertEqual(reader.read_page(b"jpeg bytes").outcome, reader.AUTH)


@override_settings(OPENROUTER_API_KEY="test-key", READER_PROVIDER="")
class SavedReadingTest(SimpleTestCase):
    def test_a_page_is_paid_for_once(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(READER_CACHE_DIR=folder):
            client = mock.Mock()
            client.chat.completions.create.return_value = response(json.dumps(PAGE))
            with mock.patch.object(reader.openai, "OpenAI", return_value=client):
                first = reader.read_page(b"same photo")
                second = reader.read_page(b"same photo")
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.content, first.content)
        self.assertEqual(second.usage.cost_usd, first.usage.cost_usd)

    def test_failed_readings_are_not_saved(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(READER_CACHE_DIR=folder):
            client = mock.Mock()
            client.chat.completions.create.return_value = response("not json")
            with mock.patch.object(reader.openai, "OpenAI", return_value=client):
                reader.read_page(b"photo")
                reader.read_page(b"photo")
        self.assertEqual(client.chat.completions.create.call_count, 2)


class OlderReadingsTest(SimpleTestCase):
    """Readings saved with an earlier prompt are still read: metrics and
    finishing a document use the stored answers, not new calls."""

    def test_a_v2_answer_without_discount(self):
        v2 = json.loads(json.dumps(PAGE))
        for line in v2["renglones"]:
            del line["descuento"]
        content = reader.to_content(v2)
        self.assertIsNone(content.lines[0].discount_percent)
        self.assertEqual(content.lines[0].unit_cost, Decimal("40.5"))
