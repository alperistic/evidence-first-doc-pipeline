"""conftest.py - pytest root configuration.

Adds the project directory to sys.path so that all test files in tests/
and tests/smoke/ can import project modules without their own sys.path
manipulation.
"""
import sys
from pathlib import Path

# Project root always first in sys.path
_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
