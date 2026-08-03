"""conftest.py – pytest root configuration.

Fügt das Projektverzeichnis zu sys.path hinzu, sodass alle Test-Dateien
in tests/ und tests/smoke/ die Projekt-Module ohne eigene sys.path-Manipulation
importieren können.
"""
import sys
from pathlib import Path

# Projektroot immer an erster Stelle in sys.path
_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
