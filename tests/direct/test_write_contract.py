"""Direct-mode tests targeting every Write Contract method of GenEscrow.

Scope: state-changing operations only (methods decorated with
``@gl.public.write`` / ``@gl.public.write.payable``). For each method we
validate the happy path (state transition, ledger effects, event/field
mutations) plus the guard clauses that raise custom ``UserError`` reverts.

Conventions (from the genlayer-dev:direct-tests skill):
- Direct mode runs the leader function only; writes and views return values
  directly, so no ``.args=`` / ``.transact()`` wrappers are used.
- Always set ``direct_vm.sender`` before invoking a write.
- ``direct_vm.value`` supplies the native amount for the payable creator.
- ``direct_vm.expect_revert(msg)`` matches the contract's ``[EXPECTED]``
  prefixed business-logic errors exactly.
- ``direct_vm.mock_llm(...)`` makes the non-deterministic dispute judgment
  deterministic so ``resolve_dispute`` can be asserted precisely.
"""

import json

import pytest

# Mirror the contract's economic constants so assertions are self-checking
# rather than hard-coded magic numbers that could silently drift.
PLATFORM_FEE_BPS = 50
BPS_DENOM = 10000
ONE_GEN = 10**18


def _addr(account) -> str:
    """Convert a direct-mode account (raw bytes) or Address to a 0x-hex string."""
    if isinstance(account, (bytes, bytearray)):
        return "0x" + account.hex()
    if hasattr(account, "hex"):
        return "0x" + account.hex()
    return str(account)


def _fee_of(gross: int) -> int:
    return (gross * PLATFORM_FEE_BPS) // BPS_DENOM


def _net_of(gross: int) -> int:
    return gross - _fee_of(gross)


# ---------------------------------------------------------------------------
# Local fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def escrow(direct_vm, direct_deploy, direct_owner, contract_path):
    """Deploy a fresh GenEscrow with a deterministic owner.

    The constructor records ``gl.message.sender_address`` as the owner, so we
    pin the deployer to ``direct_owner`` to make owner-only tests unambiguous.
    """
    direct_vm.sender = direct_owner
    return direct_deploy(contract_path)


def _fund(direct_vm, contract, buyer, seller, value=ONE_GEN, terms="Deliver the agreed item"):
    """Create + fund an escrow as ``buyer`` and return its id."""
    direct_vm.sender = buyer
    direct_vm.value = value
    return contract.create_escrow(
        _addr(seller),
        "Sample project",
        "Some description",
        terms,
        "2026-08-01",
    )


def _submit_delivery(direct_vm, contract, seller, eid, note="Delivered final files and exports"):
    """Seller records delivery, moving the escrow to DELIVERY_SUBMITTED."""
    direct_vm.sender = seller
    contract.submit_delivery(eid, note, "https://example.com/delivery")
    return eid


# ===========================================================================
# create_escrow (payable)
# ===========================================================================
def test_create_escrow_computes_fee_and_indexes(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)
    assert eid == 1

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "FUNDED"
    assert int(esc["amount_atto"]) == ONE_GEN
    # Net is gross minus the 0.50% platform fee reserved on release.
    assert int(esc["net_amount_atto"]) == _net_of(ONE_GEN)

    # Both parties are indexed for their respective lookup lists.
    assert escrow.get_escrows_by_buyer(_addr(direct_alice)) == [1]
    assert escrow.get_escrows_by_seller(_addr(direct_bob)) == [1]

    stats = escrow.get_stats()
    assert stats["total_escrows"] == 1
    assert int(stats["total_volume_atto"]) == ONE_GEN


def test_create_escrow_rejects_zero_value(escrow, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("[EXPECTED] Must send positive GEN amount"):
        escrow.create_escrow(_addr(direct_bob), "Title", "Desc", "Deliver X", "t")


def test_create_escrow_rejects_zero_address_seller(escrow, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = ONE_GEN
    with direct_vm.expect_revert("[EXPECTED] Seller cannot be the zero address"):
        escrow.create_escrow(
            "0x0000000000000000000000000000000000000000", "Title", "Desc", "Deliver X", "t"
        )


def test_create_escrow_rejects_self_dealing(escrow, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = ONE_GEN
    with direct_vm.expect_revert("[EXPECTED] Buyer and seller cannot be the same"):
        escrow.create_escrow(_addr(direct_alice), "Title", "Desc", "Deliver X", "t")


def test_create_escrow_rejects_short_title(escrow, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = ONE_GEN
    with direct_vm.expect_revert("[EXPECTED] Title must be at least 3 characters"):
        escrow.create_escrow(_addr(direct_bob), "ab", "Desc", "Deliver X", "t")


def test_create_escrow_rejects_short_terms(escrow, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = ONE_GEN
    with direct_vm.expect_revert("[EXPECTED] Terms must describe release conditions"):
        escrow.create_escrow(_addr(direct_bob), "Title", "Desc", "abc", "t")


def test_create_escrow_blocked_when_paused(escrow, direct_vm, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    escrow.set_paused(True)

    direct_vm.sender = direct_alice
    direct_vm.value = ONE_GEN
    with direct_vm.expect_revert("[EXPECTED] Contract is paused"):
        escrow.create_escrow(_addr(direct_bob), "Title", "Desc", "Deliver X", "t")


# ===========================================================================
# submit_delivery
# ===========================================================================
def test_submit_delivery_moves_to_delivery_submitted(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    escrow.submit_delivery(eid, "Delivered 3 concepts plus SVG exports", "https://example.com/files")

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "DELIVERY_SUBMITTED"
    assert "Delivered 3 concepts" in esc["delivery_note"]
    assert esc["delivery_evidence"] == "https://example.com/files"


def test_submit_delivery_rejects_non_seller(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice  # buyer cannot submit delivery
    with direct_vm.expect_revert("[EXPECTED] Only seller can submit delivery"):
        escrow.submit_delivery(eid, "Trying to self-deliver", "")


def test_submit_delivery_rejects_short_note(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Delivery note must describe what was delivered"):
        escrow.submit_delivery(eid, "hi", "")


def test_submit_delivery_rejects_double_submission(escrow, direct_vm, direct_alice, direct_bob):
    eid = _submit_delivery(direct_vm, escrow, direct_bob, _fund(direct_vm, escrow, direct_alice, direct_bob))
    # A second submission is an invalid transition out of DELIVERY_SUBMITTED.
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DELIVERY_SUBMITTED"):
        escrow.submit_delivery(eid, "Delivered again by mistake", "")


def test_submit_delivery_blocked_when_paused(escrow, direct_vm, direct_owner, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_owner
    escrow.set_paused(True)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Contract is paused"):
        escrow.submit_delivery(eid, "Delivered while paused", "")


def test_release_after_delivery_submitted(escrow, direct_vm, direct_alice, direct_bob):
    """Buyer reviews the recorded delivery, then releases funds."""
    eid = _submit_delivery(direct_vm, escrow, direct_bob, _fund(direct_vm, escrow, direct_alice, direct_bob))

    direct_vm.sender = direct_alice
    escrow.release(eid)

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "COMPLETED"
    assert escrow.get_claimable(_addr(direct_bob)) == _net_of(ONE_GEN)


def test_dispute_after_delivery_submitted(escrow, direct_vm, direct_alice, direct_bob):
    """Buyer can dispute a submitted delivery instead of releasing."""
    eid = _submit_delivery(direct_vm, escrow, direct_bob, _fund(direct_vm, escrow, direct_alice, direct_bob))

    direct_vm.sender = direct_alice
    escrow.raise_dispute(eid, "Delivered files are incomplete and wrong", "")
    assert escrow.get_escrow(eid)["status"] == "DISPUTED"


def test_claim_after_deadline_from_delivery_submitted(escrow, direct_vm, direct_alice, direct_bob):
    """Seller can time-claim after having submitted delivery."""
    eid = _submit_delivery(direct_vm, escrow, direct_bob, _fund(direct_vm, escrow, direct_alice, direct_bob))

    direct_vm.sender = direct_bob
    escrow.claim_after_deadline(eid)
    assert escrow.get_escrow(eid)["status"] == "EXPIRED"


def test_refund_blocked_after_delivery_submitted(escrow, direct_vm, direct_alice, direct_bob):
    """Buyer cannot unilaterally refund once the seller has delivered."""
    eid = _submit_delivery(direct_vm, escrow, direct_bob, _fund(direct_vm, escrow, direct_alice, direct_bob))

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DELIVERY_SUBMITTED"):
        escrow.refund(eid)


# ===========================================================================
# release
# ===========================================================================
def test_release_credits_seller_and_collects_fee(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)

    direct_vm.sender = direct_alice
    escrow.release(eid)

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "COMPLETED"
    assert int(esc["released_to_seller_atto"]) == _net_of(ONE_GEN)

    # Seller holds a claimable balance equal to the net amount.
    assert escrow.get_claimable(_addr(direct_bob)) == _net_of(ONE_GEN)
    # Platform fee is booked to the protocol accumulator.
    assert int(escrow.get_stats()["platform_fees_collected"]) == _fee_of(ONE_GEN)


def test_release_rejects_non_buyer(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob  # seller attempting release
    with direct_vm.expect_revert("[EXPECTED] Only buyer can release"):
        escrow.release(eid)


def test_release_rejects_repeat(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.release(eid)
    # Second release is an invalid state transition (already COMPLETED).
    with direct_vm.expect_revert("[EXPECTED] Invalid state: COMPLETED"):
        escrow.release(eid)


# ===========================================================================
# refund
# ===========================================================================
def test_refund_credits_buyer(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)

    direct_vm.sender = direct_alice
    escrow.refund(eid)

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "REFUNDED"
    assert int(esc["refunded_to_buyer_atto"]) == _net_of(ONE_GEN)
    assert escrow.get_claimable(_addr(direct_alice)) == _net_of(ONE_GEN)


def test_refund_rejects_non_buyer(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only buyer can request refund"):
        escrow.refund(eid)


def test_refund_rejects_after_release(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.release(eid)
    with direct_vm.expect_revert("[EXPECTED] Invalid state: COMPLETED"):
        escrow.refund(eid)


# ===========================================================================
# raise_dispute
# ===========================================================================
def test_raise_dispute_by_seller(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Buyer never responded to messages", "https://example.com/chat")

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "DISPUTED"
    assert esc["dispute_raised_by"].lower() == _addr(direct_bob).lower()
    assert "Buyer never responded" in esc["dispute_reason"]


def test_raise_dispute_by_buyer(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.raise_dispute(eid, "Seller delivered nothing at all", "")

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "DISPUTED"
    assert esc["dispute_raised_by"].lower() == _addr(direct_alice).lower()


def test_raise_dispute_rejects_outsider(escrow, direct_vm, direct_alice, direct_bob, direct_charlie):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("[EXPECTED] Only buyer or seller may raise dispute"):
        escrow.raise_dispute(eid, "I want to interfere here", "")


def test_raise_dispute_rejects_short_reason(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Dispute reason is too short"):
        escrow.raise_dispute(eid, "too short", "")


# ===========================================================================
# resolve_dispute (non-deterministic LLM judgment, mocked)
# ===========================================================================
def _open_dispute(direct_vm, contract, buyer, seller):
    eid = _fund(direct_vm, contract, buyer, seller, value=ONE_GEN)
    direct_vm.sender = buyer
    contract.raise_dispute(eid, "Seller shipped the wrong item entirely", "https://example.com/proof")
    return eid


def test_resolve_dispute_awards_seller(escrow, direct_vm, direct_alice, direct_bob):
    eid = _open_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.mock_llm(
        r".*impartial escrow judge.*",
        json.dumps({"winner": "SELLER", "release_bps": 10000, "reason": "Seller met the terms."}),
    )

    direct_vm.sender = direct_alice
    result = escrow.resolve_dispute(eid)

    assert result["winner"] == "SELLER"
    assert int(result["to_seller_atto"]) == _net_of(ONE_GEN)
    assert int(result["to_buyer_atto"]) == 0
    assert escrow.get_escrow(eid)["status"] == "RESOLVED"
    assert escrow.get_claimable(_addr(direct_bob)) == _net_of(ONE_GEN)


def test_resolve_dispute_awards_buyer(escrow, direct_vm, direct_alice, direct_bob):
    eid = _open_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.mock_llm(
        r".*impartial escrow judge.*",
        json.dumps({"winner": "BUYER", "release_bps": 0, "reason": "Non-delivery by seller."}),
    )

    direct_vm.sender = direct_alice
    result = escrow.resolve_dispute(eid)

    assert result["winner"] == "BUYER"
    assert int(result["to_buyer_atto"]) == _net_of(ONE_GEN)
    assert int(result["to_seller_atto"]) == 0
    assert escrow.get_claimable(_addr(direct_alice)) == _net_of(ONE_GEN)


def test_resolve_dispute_splits(escrow, direct_vm, direct_alice, direct_bob):
    eid = _open_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.mock_llm(
        r".*impartial escrow judge.*",
        json.dumps({"winner": "SPLIT", "release_bps": 6000, "reason": "Partial delivery."}),
    )

    direct_vm.sender = direct_alice
    result = escrow.resolve_dispute(eid)

    net = _net_of(ONE_GEN)
    expected_seller = (net * 6000) // BPS_DENOM
    assert result["winner"] == "SPLIT"
    assert int(result["to_seller_atto"]) == expected_seller
    assert int(result["to_buyer_atto"]) == net - expected_seller
    # The split must fully allocate the net amount with no leakage.
    assert int(result["to_seller_atto"]) + int(result["to_buyer_atto"]) == net


def test_resolve_dispute_requires_disputed_state(escrow, direct_vm, direct_alice, direct_bob):
    # FUNDED (never disputed) cannot be resolved.
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Invalid state: FUNDED"):
        escrow.resolve_dispute(eid)


# ===========================================================================
# claim_after_deadline
# ===========================================================================
def test_claim_after_deadline_by_seller(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)

    direct_vm.sender = direct_bob
    escrow.claim_after_deadline(eid)

    esc = escrow.get_escrow(eid)
    assert esc["status"] == "EXPIRED"
    assert escrow.get_claimable(_addr(direct_bob)) == _net_of(ONE_GEN)


def test_claim_after_deadline_rejects_non_seller(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice  # buyer cannot time-claim
    with direct_vm.expect_revert("[EXPECTED] Only seller can claim after deadline"):
        escrow.claim_after_deadline(eid)


def test_claim_after_deadline_rejects_disputed(escrow, direct_vm, direct_alice, direct_bob):
    eid = _open_dispute(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Invalid state: DISPUTED"):
        escrow.claim_after_deadline(eid)


# ===========================================================================
# claim (pull payment)
# ===========================================================================
def test_claim_zeroes_balance_and_returns_amount(escrow, direct_vm, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)
    direct_vm.sender = direct_alice
    escrow.release(eid)

    net = _net_of(ONE_GEN)
    assert escrow.get_claimable(_addr(direct_bob)) == net

    # Seller pulls their balance; the ledger entry must be cleared.
    direct_vm.sender = direct_bob
    returned = escrow.claim()
    assert int(returned) == net
    assert escrow.get_claimable(_addr(direct_bob)) == 0


def test_claim_rejects_empty_balance(escrow, direct_vm, direct_charlie):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("[EXPECTED] No claimable balance"):
        escrow.claim()


# ===========================================================================
# Admin: set_paused / withdraw_fees
# ===========================================================================
def test_set_paused_owner_only(escrow, direct_vm, direct_owner, direct_alice):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Only contract owner"):
        escrow.set_paused(True)

    direct_vm.sender = direct_owner
    escrow.set_paused(True)
    assert escrow.get_stats()["paused"] is True


def test_withdraw_fees_owner_sweeps_accumulator(escrow, direct_vm, direct_owner, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)
    direct_vm.sender = direct_alice
    escrow.release(eid)  # books the platform fee

    expected_fee = _fee_of(ONE_GEN)
    assert int(escrow.get_stats()["platform_fees_collected"]) == expected_fee

    direct_vm.sender = direct_owner
    withdrawn = escrow.withdraw_fees(_addr(direct_owner))
    assert int(withdrawn) == expected_fee
    # Accumulator is reset after the sweep.
    assert int(escrow.get_stats()["platform_fees_collected"]) == 0


def test_withdraw_fees_rejects_non_owner(escrow, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Only contract owner"):
        escrow.withdraw_fees(_addr(direct_alice))


def test_withdraw_fees_rejects_when_empty(escrow, direct_vm, direct_owner):
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED] No fees to withdraw"):
        escrow.withdraw_fees(_addr(direct_owner))


def test_withdraw_fees_rejects_zero_address(escrow, direct_vm, direct_owner, direct_alice, direct_bob):
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob, value=ONE_GEN)
    direct_vm.sender = direct_alice
    escrow.release(eid)  # book a non-zero fee so we pass the "no fees" guard

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED] Cannot withdraw to the zero address"):
        escrow.withdraw_fees("0x0000000000000000000000000000000000000000")
