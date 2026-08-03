"""
tests/test_batch3_fix4_generic_todo_filter.py

Batch-3 FIX 4: Generischer Fallback-Todo-Text wird gefiltert.
"Frist aus dem Dokument prüfen und ggf. notieren" ist ein Prompt-Schema-
Beispiel — wenn das LLM es unverändert zurückgibt, soll es unterdrückt werden.
"""
import pytest
from core.export_render import _is_generic_todo


@pytest.mark.parametrize("item,expected", [
    # Generische Prompt-Platzhalter → True (unterdrücken)
    ("Hinweis: Frist aus dem Dokument prüfen und ggf. notieren", True),
    ("Hinweis: Frist aus dem Dokument notieren", True),
    ("frist aus dem Dokument prüfen und ggf. notieren", True),
    # Konkrete To-dos → False (anzeigen)
    ("Hinweis: Mahnfrist bis 15.06.2025 notieren", False),
    ("Zu prüfen: Unterschriften vollständig", False),
    ("Bitte klären: Zahlungsmodalitäten mit Mandant", False),
    ("", False),
])
def test_is_generic_todo(item, expected):
    assert _is_generic_todo(item) == expected, f"item={item!r}: expected {expected}"
