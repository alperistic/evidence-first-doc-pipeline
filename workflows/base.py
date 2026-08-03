"""Basisklasse für alle LATE Workflows. LexGuard (governance/) läuft automatisch für jeden Workflow."""

from abc import ABC, abstractmethod
from typing import Any


class BaseWorkflow(ABC):
    """Basisklasse für alle LATE Workflows. LexGuard (governance/) läuft automatisch für jeden Workflow."""

    @property
    @abstractmethod
    def doc_class(self) -> str:
        """Dokumentklasse, für die dieser Workflow zuständig ist."""
        ...

    @abstractmethod
    def process(self, pages: list[Any], policy: Any, redaction_context: Any) -> dict:
        """
        Verarbeitet die Seiten eines Dokuments und gibt ein strukturiertes Ergebnis zurück.

        Args:
            pages: Liste der extrahierten Seiteninhalte.
            policy: Geladene PolicyConfig / LoadedPolicy.
            redaction_context: RedactionContext aus governance/redaction.py.

        Returns:
            Strukturiertes Ergebnis-Dict.
        """
        ...
