"""
Integration smoke tests for gen-escrow.

These run against a real GenLayer environment (GLSim / Studio / testnet)
and exercise full consensus including non-deterministic dispute resolution.

Run:
  gltest tests/integration/ -v -s
  gltest tests/integration/ -v -s --network studionet
"""
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


def test_deploy_and_create_escrow():
    factory = get_contract_factory("GenEscrow")
    contract = factory.deploy(args=[])

    # Use a test account as seller (the factory usually provides funded accounts)
    seller = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # example anvil-style addr; replace if needed

    tx = contract.create_escrow(
        args=[seller, "Integration Test Escrow", "desc", "Deliver the thing", "soon"]
    ).transact(value=10**18)  # 1 GEN

    assert tx_execution_succeeded(tx)

    # Read state via view
    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "FUNDED"
    assert esc["title"] == "Integration Test Escrow"


# The full dispute + AI resolution flow, value-movement checks, and every other
# write method are covered in test_lifecycle.py (which uses the correct
# `contract.connect(account)` sender API and FINALIZED waits for payouts).
