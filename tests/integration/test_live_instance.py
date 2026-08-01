"""One-shot live verification against a specific deployed GenEscrow instance.

Unlike test_lifecycle.py (which deploys a fresh contract per test), this attaches
to the already-deployed address and sends real transactions to prove the specific
on-chain instance accepts writes and reaches validator consensus.

WARNING: every run mutates the deployed contract (creates real escrows on-chain).
These tests are therefore gated OFF by default and skipped during normal sweeps.
Run on demand by setting RUN_LIVE_INSTANCE=1:
  RUN_LIVE_INSTANCE=1 gltest tests/integration/test_live_instance.py -v -s --network studionet
"""
import os

import pytest
from gltest import get_contract_factory, get_accounts, create_account
from gltest.assertions import tx_execution_succeeded
from helpers import assert_real_consensus

# Module-level gate: skip unless explicitly opted in, so default `gltest`
# sweeps never send live, state-mutating transactions to the deployed instance.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_INSTANCE") != "1",
    reason="Live-instance tests mutate the deployed contract; "
           "set RUN_LIVE_INSTANCE=1 to run on demand.",
)

LIVE_ADDRESS = "0x4588A9A9F87500961260885F9C9D23CFC9e9fa2B"
ONE_GEN = 10**18


def test_live_create_escrow_and_read_back():
    accts = get_accounts()
    buyer = accts[0]
    seller = accts[1] if len(accts) > 1 else create_account()

    factory = get_contract_factory("GenEscrow")
    contract = factory.build_contract(LIVE_ADDRESS)  # attach to the deployed instance

    stats_before = contract.get_stats().call()
    total_before = int(stats_before["total_escrows"])
    print("\n[live] address:", LIVE_ADDRESS)
    print("[live] stats before:", {k: stats_before[k] for k in
                                    ("total_escrows", "total_volume_atto", "paused")})

    tx = contract.connect(buyer).create_escrow(
        args=[
            seller.address,
            "Live verification escrow",
            "Automated on-chain write check for the deployed instance",
            "Release upon reviewer confirmation of deployment",
            "2026-08-01",
        ]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx), "live create_escrow did not succeed"
    print("[live] create_escrow consensus:", assert_real_consensus(tx))

    stats_after = contract.get_stats().call()
    total_after = int(stats_after["total_escrows"])
    assert total_after == total_before + 1, (
        f"total_escrows did not increment: {total_before} -> {total_after}"
    )

    new_id = total_after  # ids are sequential starting at 1
    esc = contract.get_escrow(args=[new_id]).call()
    print("[live] new escrow id:", new_id, "status:", esc["status"],
          "amount_atto:", esc["amount_atto"])

    assert esc["status"] == "FUNDED"
    assert int(esc["amount_atto"]) == ONE_GEN
    assert esc["buyer"].lower() == buyer.address.lower()
    assert esc["seller"].lower() == seller.address.lower()


def test_live_dispute_resolution_reaches_consensus():
    """Exercise the LLM equivalence-principle skill on the DEPLOYED instance.

    Creates its own escrow on the live address with accounts controlled this run
    (studionet get_accounts() are ephemeral per run, so we cannot dispute escrows
    created by earlier runs), then raises a dispute and resolves it via GenLayer's
    native LLM + equivalence-tolerance consensus, asserting MAJORITY_AGREE."""
    accts = get_accounts()
    buyer = accts[0]
    seller = accts[1] if len(accts) > 1 else create_account()

    factory = get_contract_factory("GenEscrow")
    contract = factory.build_contract(LIVE_ADDRESS)

    total_before = int(contract.get_stats().call()["total_escrows"])

    # 1) Create + fund a dedicated escrow for the dispute (deterministic write).
    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Consensus verification escrow", "d",
              "Deliver the report per the agreed acceptance criteria", "2026-08-01"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)
    eid = total_before + 1
    print("\n[live-consensus] created escrow id:", eid,
          "| create consensus:", assert_real_consensus(tx))

    # 2) Raise dispute as the buyer (deterministic write -> consensus).
    tx = contract.connect(buyer).raise_dispute(
        args=[eid, "Seller delivered nothing that meets the acceptance criteria.",
              "https://example.com/evidence"]
    ).transact()
    assert tx_execution_succeeded(tx)
    print("[live-consensus] raise_dispute consensus:", assert_real_consensus(tx))
    assert contract.get_escrow(args=[eid]).call()["status"] == "DISPUTED"

    # 3) Resolve via LLM + equivalence principle (non-deterministic -> validator tolerance).
    tx = contract.connect(buyer).resolve_dispute(args=[eid]).transact()
    assert tx_execution_succeeded(tx), "resolve_dispute did not succeed on the live instance"
    info = assert_real_consensus(tx)
    print("[live-consensus] resolve_dispute consensus:", info)
    assert info["result_name"] == "MAJORITY_AGREE", (
        f"expected MAJORITY_AGREE consensus, got {info['result_name']}"
    )

    resolved = contract.get_escrow(args=[eid]).call()
    print("[live-consensus] resolved winner:", resolved["resolved_winner"],
          "| release_bps:", resolved["resolved_release_bps"],
          "| status:", resolved["status"])
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolved_winner"] in ("BUYER", "SELLER", "SPLIT")
