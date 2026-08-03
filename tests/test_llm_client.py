"""
test_llm_client.py – Unit Tests für core/llm_client.py

Getestet ohne echte Azure-Verbindung:
  - _parse_response_json   (JSON-Parsing mit Fallback-Extraktion)
  - _trim_text_for_budget  (Input-Truncation)
  - _ensure_env            (Env-Var-Checks)
  - _read_usage_value      (Token-Felder aus Usage-Objekt/Dict)
  - _extract_usage_counts  (Komplett-Extraktion aus Response)
  - _invoke_json_task      (Retry-Logik, json_ok / json_fallback / api_error)
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from core.llm_client import (
    _ensure_env,
    _extract_usage_counts,
    _invoke_json_task,
    _parse_response_json,
    _read_usage_value,
    _trim_text_for_budget,
)
from core.models import BaseModel, LLMResult


# ---------------------------------------------------------------------------
# Minimale Test-Schemas
# ---------------------------------------------------------------------------

class _OkSchema(BaseModel):
    """Hat nur optionale Felder – model_validate({}) gelingt immer."""
    value: str = "ok"


class _RequiredSchema(BaseModel):
    """Hat ein Pflicht-Feld ohne Default – model_validate({}) schlägt fehl."""
    required_value: str


# ---------------------------------------------------------------------------
# Hilfsfunktion: Mock-Response bauen
# ---------------------------------------------------------------------------

def _mock_response(
    content: str = "{}",
    finish_reason: str = "stop",
    model: str = "gpt-4o",
    total: int | None = 100,
    prompt: int | None = 70,
    completion: int | None = 30,
) -> MagicMock:
    usage = MagicMock()
    usage.total_tokens = total
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion

    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


def _patched_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-api-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-test")


# ===========================================================================
# _parse_response_json
# ===========================================================================

class TestParseResponseJson:
    def test_valides_json_wird_geparst(self):
        data, err = _parse_response_json('{"key": "value", "num": 42}')
        assert data == {"key": "value", "num": 42}
        assert err is None

    def test_json_in_prosatext_extrahiert(self):
        content = 'Hier ist das Ergebnis: {"result": "ok"} Ende.'
        data, err = _parse_response_json(content)
        assert data == {"result": "ok"}
        assert err is None

    def test_json_mit_markdown_codeblock(self):
        content = "```json\n{\"status\": \"done\"}\n```"
        data, err = _parse_response_json(content)
        assert data == {"status": "done"}
        assert err is None

    def test_kein_json_gibt_none_zurueck(self):
        data, err = _parse_response_json("Kein JSON hier, nur Text.")
        assert data is None
        assert err is not None

    def test_leerer_string_gibt_none(self):
        data, err = _parse_response_json("")
        assert data is None
        assert err is not None

    def test_kaputtes_json_gibt_none(self):
        data, err = _parse_response_json('{"broken": "json"')
        assert data is None
        assert err is not None

    def test_json_array_wird_geparst(self):
        # _parse_response_json gibt gültiges JSON zurück – auch Arrays (type hint ist defensiv)
        data, err = _parse_response_json("[1, 2, 3]")
        assert data == [1, 2, 3]
        assert err is None


# ===========================================================================
# _trim_text_for_budget
# ===========================================================================

class TestTrimTextForBudget:
    def test_ohne_env_kein_trimmen(self, monkeypatch):
        monkeypatch.delenv("LATE_INPUT_MAX_CHARS", raising=False)
        text, truncated = _trim_text_for_budget("A" * 5000)
        assert len(text) == 5000
        assert truncated is False

    def test_max_chars_0_kein_trimmen(self, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "0")
        text, truncated = _trim_text_for_budget("A" * 5000)
        assert len(text) == 5000
        assert truncated is False

    def test_text_kuerzer_als_max_kein_trimmen(self, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "1000")
        text, truncated = _trim_text_for_budget("A" * 500)
        assert len(text) == 500
        assert truncated is False

    def test_text_laenger_als_max_wird_getrimmt(self, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "100")
        original = "B" * 200
        text, truncated = _trim_text_for_budget(original)
        assert len(text) == 100
        assert truncated is True

    def test_text_exakt_max_kein_trimmen(self, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "50")
        text, truncated = _trim_text_for_budget("C" * 50)
        assert len(text) == 50
        assert truncated is False


# ===========================================================================
# _ensure_env
# ===========================================================================

class TestEnsureEnv:
    def test_alle_vars_gesetzt_kein_fehler(self, monkeypatch):
        _patched_env(monkeypatch)
        _ensure_env()  # darf nicht werfen

    def test_fehlt_endpoint(self, monkeypatch):
        _patched_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT")
        with pytest.raises(EnvironmentError) as exc:
            _ensure_env()
        assert "AZURE_OPENAI_ENDPOINT" in str(exc.value)

    def test_fehlt_api_key(self, monkeypatch):
        _patched_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY")
        with pytest.raises(EnvironmentError) as exc:
            _ensure_env()
        assert "AZURE_OPENAI_API_KEY" in str(exc.value)

    def test_fehlt_deployment(self, monkeypatch):
        _patched_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT")
        with pytest.raises(EnvironmentError) as exc:
            _ensure_env()
        assert "AZURE_OPENAI_DEPLOYMENT" in str(exc.value)

    def test_alle_fehlen_alle_im_fehlertext(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
        with pytest.raises(EnvironmentError) as exc:
            _ensure_env()
        msg = str(exc.value)
        assert "AZURE_OPENAI_ENDPOINT" in msg
        assert "AZURE_OPENAI_API_KEY" in msg
        assert "AZURE_OPENAI_DEPLOYMENT" in msg


# ===========================================================================
# _read_usage_value
# ===========================================================================

class TestReadUsageValue:
    def test_dict_mit_primaer_key(self):
        usage = {"total_tokens": 150}
        assert _read_usage_value(usage, "total_tokens") == 150

    def test_dict_fallback_auf_zweiten_key(self):
        usage = {"input_tokens": 80}
        assert _read_usage_value(usage, "prompt_tokens", "input_tokens") == 80

    def test_objekt_attribut(self):
        usage = SimpleNamespace(completion_tokens=30)
        assert _read_usage_value(usage, "completion_tokens") == 30

    def test_kein_passender_key_gibt_none(self):
        usage = {"unrelated": 999}
        assert _read_usage_value(usage, "total_tokens") is None

    def test_none_wert_wird_uebersprungen(self):
        usage = {"prompt_tokens": None, "input_tokens": 55}
        assert _read_usage_value(usage, "prompt_tokens", "input_tokens") == 55

    def test_negativer_wert_gibt_none(self):
        usage = {"total_tokens": -1}
        assert _read_usage_value(usage, "total_tokens") is None

    def test_string_zahl_wird_konvertiert(self):
        usage = {"total_tokens": "200"}
        assert _read_usage_value(usage, "total_tokens") == 200


# ===========================================================================
# _extract_usage_counts
# ===========================================================================

class TestExtractUsageCounts:
    def test_usage_objekt_alle_felder(self):
        usage = SimpleNamespace(total_tokens=100, prompt_tokens=70, completion_tokens=30)
        resp = SimpleNamespace(usage=usage)
        total, prompt, completion = _extract_usage_counts(resp)
        assert total == 100
        assert prompt == 70
        assert completion == 30

    def test_usage_dict_in_response_dict(self):
        resp = {"usage": {"total_tokens": 200, "prompt_tokens": 120, "completion_tokens": 80}}
        total, prompt, completion = _extract_usage_counts(resp)
        assert total == 200
        assert prompt == 120
        assert completion == 80

    def test_keine_usage_gibt_none_tuple(self):
        resp = SimpleNamespace(usage=None)
        assert _extract_usage_counts(resp) == (None, None, None)

    def test_total_wird_aus_prompt_plus_completion_berechnet(self):
        usage = SimpleNamespace(total_tokens=None, prompt_tokens=60, completion_tokens=40)
        resp = SimpleNamespace(usage=usage)
        total, prompt, completion = _extract_usage_counts(resp)
        assert total == 100  # 60 + 40
        assert prompt == 60
        assert completion == 40

    def test_input_tokens_fallback_fuer_prompt(self):
        usage = {"input_tokens": 50, "output_tokens": 25}
        resp = {"usage": usage}
        total, prompt, completion = _extract_usage_counts(resp)
        assert prompt == 50
        assert completion == 25


# ===========================================================================
# _invoke_json_task – mit gemocktem _get_client
# ===========================================================================

@pytest.fixture
def env_vars(monkeypatch):
    """Setzt alle erforderlichen Umgebungsvariablen."""
    _patched_env(monkeypatch)
    monkeypatch.delenv("LATE_INPUT_MAX_CHARS", raising=False)


class TestInvokeJsonTask:
    def test_happy_path_json_ok(self, env_vars):
        response = _mock_response(content='{"value": "test-result"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Testdokument",
                system_prompt="Zusammenfasse.",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert isinstance(result, LLMResult)
        assert result.output_type == "json_ok"
        assert result.parsed.get("value") == "test-result"
        assert result.token_count == 100

    def test_token_counts_im_result(self, env_vars):
        response = _mock_response(total=300, prompt=200, completion=100)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.token_count == 300
        assert result.prompt_tokens == 200
        assert result.completion_tokens == 100
        assert result.has_token_usage is True

    def test_model_aus_response_nicht_deployment(self, env_vars):
        response = _mock_response(model="gpt-4o-deployment-xyz")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.model == "gpt-4o-deployment-xyz"

    def test_schema_validierung_fehler_gibt_json_fallback(self, env_vars):
        # _RequiredSchema benötigt 'required_value' → validation error bei {}
        response = _mock_response(content="{}")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_RequiredSchema,
                max_output_tokens=500,
            )

        assert result.output_type == "json_fallback"
        assert result.parsed.get("parse_error") is True

    def test_kein_json_in_antwort_gibt_json_fallback(self, env_vars):
        response = _mock_response(content="Das Modell hat keinen JSON zurückgegeben.")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.output_type == "json_fallback"
        assert result.parsed.get("parse_error") is True

    def test_api_exception_gibt_api_error(self, env_vars):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("Verbindungsfehler")

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.output_type == "api_error"
        assert result.parsed.get("api_error") is True
        assert "Verbindungsfehler" in result.parsed.get("message", "")
        assert result.token_count == 0
        assert result.has_token_usage is False

    def test_finish_reason_length_loest_retry_aus(self, env_vars):
        first_response = _mock_response(
            content='{"value": "abgeschni',
            finish_reason="length",
        )
        retry_response = _mock_response(
            content='{"value": "vollstaendig"}',
            finish_reason="stop",
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [first_response, retry_response]

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Langer Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert mock_client.chat.completions.create.call_count == 2
        assert result.output_type == "json_ok"
        assert result.parsed.get("value") == "vollstaendig"

    def test_retry_token_budget_groesser(self, env_vars):
        """Retry muss mit mehr max_tokens aufgerufen werden als erster Call."""
        first_response = _mock_response(finish_reason="length")
        retry_response = _mock_response(content='{"value": "ok"}', finish_reason="stop")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [first_response, retry_response]

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            _invoke_json_task(
                text="Text",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=800,
            )

        first_call_tokens = mock_client.chat.completions.create.call_args_list[0][1]["max_tokens"]
        retry_call_tokens = mock_client.chat.completions.create.call_args_list[1][1]["max_tokens"]
        assert retry_call_tokens > first_call_tokens

    def test_input_truncated_flag(self, env_vars, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "10")
        response = _mock_response()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Das ist ein sehr langer Text der definitiv über 10 Zeichen geht.",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.input_truncated is True
        assert result.input_chars == 10

    def test_kein_truncated_bei_kurzem_input(self, env_vars, monkeypatch):
        monkeypatch.setenv("LATE_INPUT_MAX_CHARS", "1000")
        response = _mock_response()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response

        with patch("core.llm_client._get_client", return_value=(mock_client, "gpt-4o-test")):
            result = _invoke_json_task(
                text="Kurz.",
                system_prompt="Prompt",
                model_schema=_OkSchema,
                max_output_tokens=500,
            )

        assert result.input_truncated is False

    def test_fehlende_env_vars_wirft_vor_api_call(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
        mock_client = MagicMock()

        with patch("core.llm_client._get_client", return_value=(mock_client, "test")):
            with pytest.raises(EnvironmentError):
                _invoke_json_task(
                    text="Text",
                    system_prompt="Prompt",
                    model_schema=_OkSchema,
                    max_output_tokens=500,
                )

        # _get_client / API wurde nie aufgerufen
        mock_client.chat.completions.create.assert_not_called()
