"""Base class for all pipeline workflows. The governance layer (governance/) runs automatically for every workflow."""

from abc import ABC, abstractmethod
from typing import Any


class BaseWorkflow(ABC):
    """Base class for all pipeline workflows. The governance layer (governance/) runs automatically for every workflow."""

    @property
    @abstractmethod
    def doc_class(self) -> str:
        """Document class this workflow is responsible for."""
        ...

    @abstractmethod
    def process(self, pages: list[Any], policy: Any, redaction_context: Any) -> dict:
        """
        Processes a document's pages and returns a structured result.

        Args:
            pages: List of extracted page contents.
            policy: Loaded PolicyConfig / LoadedPolicy.
            redaction_context: RedactionContext from governance/redaction.py.

        Returns:
            Structured result dict.
        """
        ...
