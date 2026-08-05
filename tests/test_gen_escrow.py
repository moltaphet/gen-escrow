"""GenSkills benchmark suite for GenEscrow.

This file implements the 4 core GenSkills the reviewer flagged as missing.
Each test is a direct-mode (leader-only, in-memory) test using the
``genlayer-test`` pytest plugin fixtures. It runs with:

    pytest tests/test_gen_escrow.py -v

(``genvm test`` is not a binary in this toolchain; GenLayer direct-mode tests
are executed through the ``gltest`` / ``pytest`` runner shipped by
``genlayer-test``.)

Naming note - the reviewer's benchmark uses generic names that map onto the
shipped contract's concrete API:

    benchmark term        -> GenEscrow implementation
    -------------------      --------------------------------------------
    seller_claim()        -> claim_after_deadline()  (time-locked payout)
    buyer_evidence        -> buyer_dispute_evidence   (attributable record)
    seller_evidence       -> seller_dispute_evidence  (attributable record)
    UNDER_ARBITRATION     -> "DISPUTED"               (arbitration state)
    gl.nondet.web.render  -> live evidence rendering inside run_nondet_unsafe
"""

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Contract under test. This file lives at tests/ (outside tests/direct/), so it
# resolves the contract path itself rather than depending on the direct/
# conftest ``contract_path`` fixture.
# ---------------------------------------------------------------------------
CONTRACT_PATH = str(Path(__file__).parent.parent / "contracts" / "genescrow.py")

# Mirror the contract's economic constants so assertions are self-checking.
PLATFORM_FEE_BPS = 50
BPS_DENOM = 10000
ONE_GEN = 10**18
VALID_WINNERS = ("BUYER", "SELLER", "SPLIT")


def _addr(account) -> str:
    """Convert a direct-mode account (raw bytes) or Address to a 0x-hex string."""
    if isinstance(account, (bytes, bytearray)):
        return "0x" + account.hex()
    if hasattr(account, "hex"):
        return "0x" + account.hex()
    return str(account)


def _net_of(gross: int) -> int:
    return gross - (gross * PLATFORM_FEE_BPS) // BPS_DENOM


@pytest.fixture
def contract(direct_vm, direct_deploy, direct_owner):
    """Deploy a fresh GenEscrow, pinning the deployer as the owner."""
    direct_vm.sender = direct_owner
    return direct_deploy(CONTRACT_PATH)


def _fund(direct_vm, c, buyer, seller, value=ONE_GEN, deadline="2026-08-01"):
    """Create + fund an escrow as ``buyer`` and return its id."""
    direct_vm.sender = buyer
    direct_vm.value = value
    return c.create_escrow(
        _addr(seller),
        "Sample project",
        "Some description",
        "Deliver the agreed item",
        deadline,
    )


# ===========================================================================
# GenSkill #1: Premature Claim Guard (Time-Lock Verification)
#
# Calling the seller's time-locked payout BEFORE the inspection deadline must
# fail and revert cleanly, leaving the funds locked and the state untouched.
# ===========================================================================
def test_genskill_1_premature_claim_guard(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.warp("2026-07-01T00:00:00+00:00")
    eid = _fund(direct_vm, contract, direct_alice, direct_bob, deadline="2026-08-01")

    # Still well before the objective deadline -> the seller's claim must revert.
    direct_vm.warp("2026-07-15T00:00:00+00:00")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert(
        "[EXPECTED] Deadline has not passed; seller cannot claim yet"
    ):
        contract.claim_after_deadline(eid)

    # Clean revert: no payout, funds still locked, state unchanged.
    esc = contract.get_escrow(eid)
    assert esc["status"] == "FUNDED"
    assert contract.get_claimable(_addr(direct_bob)) == 0

    # Time-lock releases exactly at/after the deadline (proves it is a lock, not
    # an unconditional block).
    direct_vm.warp("2026-08-01T00:00:00+00:00")
    direct_vm.sender = direct_bob
    contract.claim_after_deadline(eid)
    assert contract.get_escrow(eid)["status"] == "EXPIRED"
    assert contract.get_claimable(_addr(direct_bob)) == _net_of(ONE_GEN)


# ===========================================================================
# GenSkill #2: Evidence Attribution & Anti-Overwrite Check
#
# buyer_dispute_evidence and seller_dispute_evidence live in separate,
# individually attributable storage entries, and neither party can overwrite
# their own or the counterparty's record.
# ===========================================================================
def test_genskill_2_evidence_attribution_and_anti_overwrite(
    contract, direct_vm, direct_alice, direct_bob
):
    eid = _fund(direct_vm, contract, direct_alice, direct_bob)

    # Buyer and seller each file their OWN evidence.
    direct_vm.sender = direct_alice
    contract.raise_dispute(
        eid, "Seller delivered the wrong files entirely", "https://buyer.example/proof"
    )
    direct_vm.sender = direct_bob
    contract.raise_dispute(
        eid, "Buyer refused to accept the correct delivery", "https://seller.example/proof"
    )

    esc = contract.get_escrow(eid)
    # Separately attributable: each side's evidence is isolated in its own field.
    assert esc["buyer_dispute_evidence"] == "https://buyer.example/proof"
    assert esc["seller_dispute_evidence"] == "https://seller.example/proof"
    assert esc["buyer_dispute_evidence"] != esc["seller_dispute_evidence"]
    assert "wrong files" in esc["buyer_dispute_reason"]
    assert "refused to accept" in esc["seller_dispute_reason"]
    # First opener is preserved as attribution.
    assert esc["dispute_raised_by"].lower() == _addr(direct_alice).lower()

    # Anti-overwrite: buyer cannot clobber their own record.
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert(
        "[EXPECTED] Buyer dispute record already exists and cannot be overwritten"
    ):
        contract.raise_dispute(eid, "Rewriting the buyer record entirely", "https://buyer.example/tampered")

    # Anti-overwrite: seller cannot clobber their own record either.
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert(
        "[EXPECTED] Seller dispute record already exists and cannot be overwritten"
    ):
        contract.raise_dispute(eid, "Rewriting the seller record entirely", "https://seller.example/tampered")

    # Both original, attributable records survive the tamper attempts intact.
    esc = contract.get_escrow(eid)
    assert esc["buyer_dispute_evidence"] == "https://buyer.example/proof"
    assert esc["seller_dispute_evidence"] == "https://seller.example/proof"


# ===========================================================================
# GenSkill #3: State Guard & Dispute Race Resistance
#
# Once a dispute is flagged the status transitions to the arbitration state
# (DISPUTED; the contract's equivalent of UNDER_ARBITRATION) and every
# concurrent state-change attempt is rejected.
# ===========================================================================
def test_genskill_3_state_guard_and_dispute_race_resistance(
    contract, direct_vm, direct_alice, direct_bob
):
    direct_vm.warp("2026-07-01T00:00:00+00:00")
    eid = _fund(direct_vm, contract, direct_alice, direct_bob)

    # Flag the dispute -> transitions into the arbitration state.
    direct_vm.sender = direct_alice
    contract.raise_dispute(eid, "Seller shipped the wrong item entirely", "")
    assert contract.get_escrow(eid)["status"] == "DISPUTED"

    # Concurrent state-change attempts during arbitration must all be rejected:

    # Buyer cannot release out from under arbitration.
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DISPUTED"):
        contract.release(eid)

    # Buyer cannot refund out from under arbitration.
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DISPUTED"):
        contract.refund(eid)

    # Seller cannot race a time-claim past arbitration (even after the deadline).
    direct_vm.warp("2026-09-01T00:00:00+00:00")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DISPUTED"):
        contract.claim_after_deadline(eid)

    # Seller cannot submit a fresh delivery to mutate arbitration state.
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DISPUTED"):
        contract.submit_delivery(eid, "Trying to deliver after dispute", "")

    # State is still the untouched arbitration state after all races.
    assert contract.get_escrow(eid)["status"] == "DISPUTED"


# ===========================================================================
# GenSkill #4: Non-Deterministic Web Render & LLM Consensus Test
#
# Dispute evidence URLs are fetched via gl.nondet.web.render() and the
# resolution runs strictly through gl.vm.run_nondet_unsafe, yielding a
# well-formed consensus outcome (valid winner + bounded release_bps). The web
# mock keys on the URL and the LLM mock keys on the RENDERED page token, so a
# passing test proves render() fired inside the non-deterministic block and its
# output flowed into consensus.
# ===========================================================================
def test_genskill_4_nondet_web_render_and_llm_consensus(
    contract, direct_vm, direct_alice, direct_bob
):
    eid = _fund(direct_vm, contract, direct_alice, direct_bob)

    # Seller records a delivery whose proof is a live link.
    direct_vm.sender = direct_bob
    contract.submit_delivery(
        eid, "Delivered final files at the link", "Proof: https://drive.example/deliverable"
    )
    direct_vm.sender = direct_alice
    contract.raise_dispute(eid, "I do not think the files were actually delivered", "")
    # The seller forfeits its reply, which unlocks AI resolution immediately
    # instead of waiting out the 48h counter-evidence window. Window enforcement
    # itself is covered in tests/direct/test_dispute_response_window.py.
    direct_vm.sender = direct_bob
    contract.waive_dispute_response(eid)

    # The live page renders to this body inside the non-deterministic block.
    direct_vm.mock_web(
        r"https://drive\.example/deliverable",
        {"method": "GET", "status": 200, "body": "RENDER_TOKEN_4c1 final logo AI + SVG exports"},
    )
    # The LLM mock ONLY fires when the RENDERED token reached the prompt, proving
    # gl.nondet.web.render() output flowed into the consensus judgment.
    direct_vm.mock_llm(
        r".*RENDER_TOKEN_4c1.*",
        json.dumps({"winner": "SELLER", "release_bps": 10000, "reason": "Verified deliverable present."}),
    )

    direct_vm.sender = direct_alice
    result = contract.resolve_dispute(eid)

    # gl.nondet.web.render() was actually invoked for the evidence URL.
    assert len(direct_vm._web_mocks_hit) >= 1

    # run_nondet_unsafe produced a well-formed consensus outcome: a valid winner
    # and a bounded release proportion (the "valid boolean outcome" of the
    # validator comparator manifests as a resolved, conserved payout).
    assert result["winner"] in VALID_WINNERS
    assert 0 <= int(result["release_bps"]) <= BPS_DENOM
    assert result["winner"] == "SELLER"
    assert int(result["to_seller_atto"]) == _net_of(ONE_GEN)
    assert int(result["to_buyer_atto"]) == 0
    # Conservation: net payouts fully allocated, nothing stranded by consensus.
    assert int(result["to_seller_atto"]) + int(result["to_buyer_atto"]) == _net_of(ONE_GEN)
    assert contract.get_escrow(eid)["status"] == "RESOLVED"
