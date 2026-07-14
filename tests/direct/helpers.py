"""Reusable helpers for direct-mode tests."""
from pathlib import Path

CONTRACT_PATH = Path(__file__).parent.parent.parent / "contracts" / "genescrow.py"


def load_contract_source() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")
