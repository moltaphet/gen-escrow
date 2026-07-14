"""Direct mode test configuration for gen-escrow."""
import pytest
from pathlib import Path

# Shared fixtures provided by genlayer-test pytest plugin:
# - direct_vm
# - direct_deploy
# - direct_alice, direct_bob, direct_charlie, direct_owner

CONTRACT_PATH = Path(__file__).parent.parent.parent / "contracts" / "genescrow.py"


@pytest.fixture(scope="session")
def contract_path():
    return str(CONTRACT_PATH)
