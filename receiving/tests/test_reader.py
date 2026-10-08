import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from receiving import reader

READING = {
    "proveedor": "ACME TOOLS",
    "tipo_documento": "Remision",
    "folio": "R-0001",
    "fecha": "2026-01-15",
    "subtotal": 1234.50,
    "total": None,
    "renglones": [
        {
            "cantidad": 2,
            "unidad": None,
            "clave": "AT-1001",
            "descripcion": "GARDEN HOSE 1/2",
            "costo_unitario": 40.50,
            "importe": 81.00,
            "confianza": 0.95,
        }
    ],
}


def fake_response(content):
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=900,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        model_extra={"cost": 0.00150000},
    )
    choice = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage, model_extra={"provider": "provider-x"})


@override_settings(OPENROUTER_API_KEY="test-key")
class ReaderTest(SimpleTestCase):
    """The reader without calling OpenRouter: the client is replaced."""

    def read(self, content):
        client = mock.Mock()
        client.chat.completions.create.return_value = fake_response(content)
        with mock.patch.object(reader, "OpenAI", return_value=client):
            return reader.read_sheet(b"fake image")

    def test_maps_the_reading_to_english_names(self):
        reading = self.read(json.dumps(READING))
        self.assertEqual(reading.supplier_name, "ACME TOOLS")
        self.assertIsNone(reading.total)
        line = reading.lines[0]
        self.assertEqual(line.unit, "")
        self.assertEqual(line.supplier_code, "AT-1001")
        # Through str: no float noise in money.
        self.assertEqual(line.unit_cost, Decimal("40.50"))
        self.assertEqual(reading.usage.cost_usd, Decimal("0.00150000"))
        self.assertEqual(reading.usage.input_tokens, 1200)

    def test_strips_json_fences(self):
        reading = self.read("```json\n" + json.dumps(READING) + "\n```")
        self.assertEqual(len(reading.lines), 1)

    def test_an_answer_outside_the_schema_is_rejected_with_its_usage(self):
        bad = {**READING, "renglones": [{"cantidad": 2}]}
        with self.assertRaises(reader.ReaderError) as caught:
            self.read(json.dumps(bad))
        self.assertEqual(caught.exception.usage.output_tokens, 900)

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(reader.ReaderError):
            self.read("not json")

    @override_settings(OPENROUTER_API_KEY="")
    def test_without_key_it_says_so(self):
        with self.assertRaises(reader.ReaderError):
            reader.read_sheet(b"fake image")
