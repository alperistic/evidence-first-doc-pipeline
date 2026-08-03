"""
tests/test_fix1b_auto_classify_default.py

FIX 4: auto_classify ist kein Toggle mehr – hardcoded True als fester Pipeline-Bestandteil.
Prüft direkt die Quellcodedatei.
"""
import re
import pathlib


def test_auto_classify_default_is_true():
    """auto_classify muss in app.py als True gesetzt sein (kein Toggle mehr)."""
    app_source = pathlib.Path("app.py").read_text(encoding="utf-8")
    # Entweder hardcoded True oder Toggle mit value=True
    hardcoded = re.search(r'auto_classify\s*=\s*True\b', app_source)
    toggle = re.search(
        r'auto_classify\s*=\s*st\.toggle\([^)]*value\s*=\s*(True|False)',
        app_source,
    )
    if toggle:
        assert toggle.group(1) == "True", (
            f"auto_classify default should be True but got {toggle.group(1)!r}"
        )
    else:
        assert hardcoded is not None, (
            "auto_classify must be hardcoded True or a toggle with value=True in app.py"
        )
