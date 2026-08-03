"""tests/conftest.py – Gemeinsame pytest-Fixtures für alle Tests."""

import pytest

from core.config import reset_config


@pytest.fixture(autouse=True)
def reset_app_config():
    """Setzt den AppConfig-Singleton vor jedem Test zurück.

    Notwendig, weil Tests via monkeypatch Env-Variablen ändern –
    der Singleton würde sonst gecachte Werte liefern.
    """
    reset_config()
    yield
    reset_config()
