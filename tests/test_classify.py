"""
test_classify.py – Unit-Tests für core/analyzer.classify()

Getestet ohne echte Azure-Verbindung:
  - NameError darf nicht mehr auftreten (Regression gegen cfg-Bug)
  - Heuristik-Pfad (kein LLM-Call)
  - Erfolgreicher LLM-Aufruf mit Mock
  - LLM gibt unbekannte Klasse zurück → Fallback "Sonstiges"
  - LLM wirft Exception → Fallback "Sonstiges"
  - Fehlende Env-Vars → sofortiger Fallback "Sonstiges" (kein LLM-Call)
  - max_chars-Parameter wird respektiert
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.analyzer import classify


# ---------------------------------------------------------------------------
# Hilfsfunktion: Mock-LLM-Response bauen
# ---------------------------------------------------------------------------

def _mock_response(content: str) -> SimpleNamespace:
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice])


def _mock_cfg(
    classifier_deployment: str = "",
    preview_chars: int = 1200,
) -> MagicMock:
    cfg = MagicMock()
    cfg.azure_openai_classifier_deployment = classifier_deployment
    cfg.classify_preview_chars = preview_chars
    return cfg


# ---------------------------------------------------------------------------
# Heuristik-Pfad: kein LLM nötig
# ---------------------------------------------------------------------------

class TestHeuristicPath:
    """_heuristic_classify trifft → classify() gibt Ergebnis zurück, ohne LLM-Mock.

    Env-Vars müssen gesetzt sein, weil der Env-Check VOR der Heuristik läuft.
    """

    def test_vertrag_heuristik(self, monkeypatch):
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        result = classify("Dies ist ein Mietvertrag zwischen den Parteien.")
        assert result == "Vertrag"

    def test_klageschrift_heuristik(self, monkeypatch):
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        result = classify("Hiermit erheben wir Klageschrift gegen den Beklagten.")
        assert result == "Klageschrift"

    def test_abmahnung_heuristik(self, monkeypatch):
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        result = classify("Wir sprechen hiermit eine Abmahnung aus.")
        assert result == "Abmahnung"

    def test_mahnung_heuristik(self, monkeypatch):
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        result = classify("Dies ist eine Mahnung bezüglich der ausstehenden Zahlung.")
        assert result == "Mahnung"

    def test_schriftsatz_heuristik(self, monkeypatch):
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        result = classify("Im vorliegenden Schriftsatz nehmen wir Stellung.")
        assert result == "Schriftsatz"


# ---------------------------------------------------------------------------
# Fehlende Env-Vars → sofortiger Fallback, kein LLM-Call
# ---------------------------------------------------------------------------

class TestMissingEnvFallback:
    """Wenn Pflicht-Env-Vars fehlen, gibt classify() 'Sonstiges' zurück."""

    def test_missing_env_returns_sonstiges(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
        # Kein Heuristik-Treffer → würde LLM brauchen, aber Env fehlt
        result = classify("Dieses Dokument ist ein allgemeines Schreiben ohne Typ.")
        assert result == "Sonstiges"


# ---------------------------------------------------------------------------
# LLM-Pfad: Mock-Client
# ---------------------------------------------------------------------------

ENV_VARS = {
    "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4o-test",
}


class TestLLMPath:
    """LLM-Pfad mit gemocktem _get_client und get_config."""

    def _make_mock_client(self, content: str) -> MagicMock:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_response(content)
        return mock_client

    def test_no_nameerror(self, monkeypatch):
        """Regression: cfg war undefiniert — darf keinen NameError mehr werfen."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = self._make_mock_client("Sonstiges")
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=_mock_cfg()),
        ):
            # Kein Heuristik-Treffer, daher LLM-Pfad
            result = classify("Dieses allgemeine Dokument hat keinen bekannten Typ.")
        assert isinstance(result, str)  # NameError würde hier hochpropagieren

    def test_llm_known_class_returned(self, monkeypatch):
        """LLM gibt gültige DOC_CLASSES-Klasse zurück → classify() reicht sie durch."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = self._make_mock_client("Mahnung")
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=_mock_cfg()),
        ):
            # Text ohne Heuristik-Treffer, damit der LLM-Pfad greift
            result = classify("Dieses allgemeine Dokument hat keinen bekannten Typ.")
        assert result == "Mahnung"

    def test_llm_unknown_class_falls_back(self, monkeypatch):
        """LLM gibt unbekannte Klasse → Fallback 'Sonstiges'."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = self._make_mock_client("UnbekannteKlasse")
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=_mock_cfg()),
        ):
            result = classify("Dieses allgemeine Dokument hat keinen bekannten Typ.")
        assert result == "Sonstiges"

    def test_llm_exception_falls_back(self, monkeypatch):
        """LLM wirft Exception → Fallback 'Sonstiges', kein Crash."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=_mock_cfg()),
        ):
            result = classify("Dieses allgemeine Dokument hat keinen bekannten Typ.")
        assert result == "Sonstiges"

    def test_classifier_deployment_used_when_set(self, monkeypatch):
        """azure_openai_classifier_deployment aus Config wird als model= übergeben."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = self._make_mock_client("Sonstiges")
        cfg = _mock_cfg(classifier_deployment="gpt-4o-classifier")
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=cfg),
        ):
            classify("Dieses allgemeine Dokument hat keinen bekannten Typ.")
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o-classifier"

    def test_max_chars_parameter_truncates(self, monkeypatch):
        """max_chars wird respektiert: preview ist auf max_chars begrenzt."""
        for k, v in ENV_VARS.items():
            monkeypatch.setenv(k, v)
        mock_client = self._make_mock_client("Sonstiges")
        long_text = "x" * 5000
        with (
            patch("core.analyzer._get_client", return_value=(mock_client, "gpt-4o-test")),
            patch("core.analyzer.get_config", return_value=_mock_cfg()),
        ):
            classify(long_text, max_chars=100)
        call_kwargs = mock_client.chat.completions.create.call_args
        user_content = call_kwargs.kwargs["messages"][1]["content"]
        assert len(user_content) == 100
