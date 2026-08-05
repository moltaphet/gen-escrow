"""Direct-mode tests for the GenEscrow dispute response window.

Reviewer finding addressed here: a dispute could previously be finalized the
instant one party filed, so whoever disputed first got the case judged on their
own uncontested record before the counterparty could answer.

``resolve_dispute`` is now gated: it unlocks only when

  (a) BOTH parties have filed their own statement + evidence, or
  (b) the non-responding party explicitly calls ``waive_dispute_response``, or
  (c) the 48h response window elapses with no reply.

The suite covers each unlock path, the rejection of immediate one-sided
resolution while the window is active, the waiver's access control, and the
per-role view payload that the two UI roles (buyer / seller) render from.
"""

import json

import pytest

PLATFORM_FEE_BPS = 50
BPS_DENOM = 10000
ONE_GEN = 10**18

# Mirrors DISPUTE_RESPONSE_WINDOW_SECONDS in contracts/genescrow.py.
WINDOW_SECONDS = 48 * 60 * 60

# Fixed clock anchors. The dispute opens at OPEN_AT, so the response window
# closes exactly at DEADLINE_AT (OPEN_AT + 48h).
OPEN_AT = "2026-07-01T00:00:00+00:00"
JUST_INSIDE = "2026-07-02T23:59:59+00:00"   # 1s before the window closes
DEADLINE_AT = "2026-07-03T00:00:00+00:00"   # exactly at the boundary
WELL_AFTER = "2026-07-10T00:00:00+00:00"

# Kept far past every warp above so the delivery deadline never interferes.
FAR_DEADLINE = "2027-01-01"


def _addr(account) -> str:
    if isinstance(account, (bytes, bytearray)):
        return "0x" + account.hex()
    if hasattr(account, "hex"):
        return "0x" + account.hex()
    return str(account)


def _net_of(gross: int) -> int:
    return gross - (gross * PLATFORM_FEE_BPS) // BPS_DENOM


@pytest.fixture
def escrow(direct_vm, direct_deploy, direct_owner, contract_path):
    direct_vm.sender = direct_owner
    return direct_deploy(contract_path)


def _fund(direct_vm, contract, buyer, seller, value=ONE_GEN):
    direct_vm.sender = buyer
    direct_vm.value = value
    return contract.create_escrow(
        _addr(seller),
        "Sample project",
        "Some description",
        "Deliver the agreed item",
        FAR_DEADLINE,
    )


def _open_buyer_dispute(direct_vm, contract, buyer, seller):
    """Buyer opens a dispute at OPEN_AT; the seller has not replied yet."""
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, contract, buyer, seller)
    direct_vm.sender = buyer
    contract.raise_dispute(
        eid, "Seller delivered nothing that matches the terms", "https://buyer.example/proof"
    )
    return eid


def _mock_judgment(direct_vm, winner="BUYER", bps=0):
    direct_vm.mock_llm(
        r".*impartial escrow judge.*",
        json.dumps({"winner": winner, "release_bps": bps, "reason": "Judged on the record."}),
    )


# ===========================================================================
# (1) Immediate one-sided resolution is rejected while the window is active
# ===========================================================================
def test_resolve_rejected_immediately_after_one_sided_dispute(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The core regression: dispute + instant resolve must revert."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm)

    # Same block as the dispute - the seller has had no chance to answer.
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Dispute response window is still open"):
        escrow.resolve_dispute(eid)

    # Clean revert: nothing was judged, nothing was paid out.
    esc = escrow.get_escrow(eid)
    assert esc["status"] == "DISPUTED"
    assert esc["resolved_winner"] == ""
    assert escrow.get_claimable(_addr(direct_alice)) == 0
    assert escrow.get_claimable(_addr(direct_bob)) == 0


def test_resolve_rejected_for_every_caller_while_window_active(
    escrow, direct_vm, direct_alice, direct_bob, direct_charlie, direct_owner
):
    """The gate is on state, not on identity.

    ``resolve_dispute`` is permissionless, so the initiator, the owner and an
    unrelated third party (standing in for a validator-triggered call) must all
    be refused while the counterparty still holds the floor.
    """
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm)

    for caller in (direct_alice, direct_bob, direct_charlie, direct_owner):
        direct_vm.sender = caller
        with direct_vm.expect_revert("[EXPECTED] Dispute response window is still open"):
            escrow.resolve_dispute(eid)

    assert escrow.get_escrow(eid)["status"] == "DISPUTED"


def test_resolve_rejected_one_second_before_window_closes(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The window is held right up to its final second."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm)

    direct_vm.warp(JUST_INSIDE)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Dispute response window is still open"):
        escrow.resolve_dispute(eid)

    status = escrow.get_dispute_response_status(eid)
    assert status["can_resolve"] is False
    assert status["seconds_remaining"] == 1


def test_seller_initiated_dispute_awaits_the_buyer(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The guard is symmetric: a seller-opened dispute waits on the buyer."""
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Buyer went silent and never accepted delivery", "")
    _mock_judgment(direct_vm)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Dispute response window is still open"):
        escrow.resolve_dispute(eid)

    status = escrow.get_dispute_response_status(eid)
    assert status["awaiting_party"] == "BUYER"
    assert status["seller_responded"] is True
    assert status["buyer_responded"] is False


# ===========================================================================
# (2) Unlock path A - the window expires
# ===========================================================================
def test_resolve_unlocks_exactly_at_window_boundary(
    escrow, direct_vm, direct_alice, direct_bob
):
    """At exactly OPEN_AT + 48h the window is spent and resolution proceeds."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm, winner="BUYER", bps=0)

    direct_vm.warp(DEADLINE_AT)
    status = escrow.get_dispute_response_status(eid)
    assert status["window_expired"] is True
    assert status["seconds_remaining"] == 0
    assert status["can_resolve"] is True

    direct_vm.sender = direct_alice
    result = escrow.resolve_dispute(eid)

    assert result["winner"] == "BUYER"
    assert int(result["to_buyer_atto"]) == _net_of(ONE_GEN)
    assert escrow.get_escrow(eid)["status"] == "RESOLVED"


def test_resolve_after_window_expiry_pays_out(escrow, direct_vm, direct_alice, direct_bob):
    """A non-responding seller forfeits by silence, and the payout still lands."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm, winner="BUYER", bps=0)

    direct_vm.warp(WELL_AFTER)
    direct_vm.sender = direct_alice
    escrow.resolve_dispute(eid)

    assert escrow.get_claimable(_addr(direct_alice)) == _net_of(ONE_GEN)
    assert escrow.get_claimable(_addr(direct_bob)) == 0
    assert escrow.get_escrow(eid)["dispute_response_phase"] == "WINDOW_EXPIRED"


def test_expired_window_tells_the_judge_the_record_is_one_sided(
    escrow, direct_vm, direct_alice, direct_bob
):
    """A silent counterparty must be framed to the LLM as a forfeited reply,
    not as agreement. The mock only fires when that framing is in the prompt."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.mock_llm(
        r"RESPONSE POSTURE: The response window elapsed with NO reply",
        json.dumps({"winner": "BUYER", "release_bps": 0, "reason": "Uncontested but evidenced."}),
    )

    direct_vm.warp(WELL_AFTER)
    direct_vm.sender = direct_alice
    assert escrow.resolve_dispute(eid)["winner"] == "BUYER"


# ===========================================================================
# (3) Unlock path B - both parties file
# ===========================================================================
def test_resolve_unlocks_immediately_when_both_parties_file(
    escrow, direct_vm, direct_alice, direct_bob
):
    """No waiting once the record is genuinely two-sided - the window exists to
    protect the counterparty, and they have used it."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    escrow.raise_dispute(
        eid, "Delivery matched the agreed terms exactly", "https://seller.example/proof"
    )
    _mock_judgment(direct_vm, winner="SPLIT", bps=5000)

    status = escrow.get_dispute_response_status(eid)
    assert status["phase"] == "BOTH_SUBMITTED"
    assert status["awaiting_party"] == ""
    assert status["can_resolve"] is True

    # Still inside the 48h window, but both records exist -> allowed.
    direct_vm.sender = direct_alice
    result = escrow.resolve_dispute(eid)
    assert result["winner"] == "SPLIT"
    assert int(result["to_seller_atto"]) + int(result["to_buyer_atto"]) == _net_of(ONE_GEN)


def test_both_records_reach_the_judge(escrow, direct_vm, direct_alice, direct_bob):
    """Both statements must be in the prompt, each attributed to its own party."""
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.raise_dispute(eid, "Buyer says BUYERTOKEN_W1 nothing usable arrived", "")
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Seller says SELLERTOKEN_W2 everything was delivered", "")

    # Fires only if BOTH tokens and the two-sided posture are present.
    direct_vm.mock_llm(
        r"(?s)RESPONSE POSTURE: BOTH parties filed.*BUYERTOKEN_W1.*SELLERTOKEN_W2",
        json.dumps({"winner": "SPLIT", "release_bps": 5000, "reason": "Both sides partly right."}),
    )

    direct_vm.sender = direct_alice
    assert escrow.resolve_dispute(eid)["winner"] == "SPLIT"


# ===========================================================================
# (4) Unlock path C - explicit waiver
# ===========================================================================
def test_waiver_by_non_responding_party_unlocks_resolution(
    escrow, direct_vm, direct_alice, direct_bob
):
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    _mock_judgment(direct_vm, winner="BUYER", bps=0)

    direct_vm.sender = direct_bob
    escrow.waive_dispute_response(eid)

    esc = escrow.get_escrow(eid)
    assert esc["dispute_response_waived"] is True
    assert esc["dispute_response_waived_by"].lower() == _addr(direct_bob).lower()
    assert esc["dispute_response_waived_at"] != ""
    assert esc["dispute_response_phase"] == "RESPONSE_WAIVED"
    assert esc["can_resolve"] is True

    # Unlocks straight away, well inside the 48h window.
    direct_vm.sender = direct_alice
    assert escrow.resolve_dispute(eid)["winner"] == "BUYER"


def test_waiver_tells_the_judge_the_reply_was_forfeited(
    escrow, direct_vm, direct_alice, direct_bob
):
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    escrow.waive_dispute_response(eid)

    direct_vm.mock_llm(
        r"RESPONSE POSTURE: One party EXPLICITLY WAIVED",
        json.dumps({"winner": "BUYER", "release_bps": 0, "reason": "Waived and evidenced."}),
    )
    direct_vm.sender = direct_alice
    assert escrow.resolve_dispute(eid)["winner"] == "BUYER"


def test_waiver_rejected_from_the_party_that_already_filed(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The initiator cannot waive the other side's reply on its behalf - that is
    exactly the one-sided shortcut the window exists to block."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert(
        "[EXPECTED] Only the non-responding party may waive the dispute response"
    ):
        escrow.waive_dispute_response(eid)

    assert escrow.get_escrow(eid)["dispute_response_waived"] is False
    assert escrow.get_escrow(eid)["can_resolve"] is False


def test_waiver_rejected_from_outsider(
    escrow, direct_vm, direct_alice, direct_bob, direct_charlie
):
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert(
        "[EXPECTED] Only buyer or seller may waive the dispute response"
    ):
        escrow.waive_dispute_response(eid)


def test_waiver_rejected_when_already_waived(escrow, direct_vm, direct_alice, direct_bob):
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    escrow.waive_dispute_response(eid)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Dispute response has already been waived"):
        escrow.waive_dispute_response(eid)


def test_waiver_requires_an_open_dispute(escrow, direct_vm, direct_alice, direct_bob):
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Invalid state: FUNDED"):
        escrow.waive_dispute_response(eid)


def test_waiver_does_not_lock_out_a_party_that_changes_its_mind(
    escrow, direct_vm, direct_alice, direct_bob
):
    """A waiver forfeits the wait, not the right to be heard: until resolution
    lands the seller may still file, and the record becomes two-sided."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_bob
    escrow.waive_dispute_response(eid)

    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "On reflection the delivery did meet the terms", "")

    esc = escrow.get_escrow(eid)
    assert esc["seller_responded"] is True
    assert esc["dispute_response_phase"] == "BOTH_SUBMITTED"
    assert esc["seller_dispute_reason"] != ""


# ===========================================================================
# (5) Dual UI role scenarios
#
# The frontend renders from get_escrow / get_dispute_response_status, so these
# assert the exact payload each role's UI reads, and that a write from one role
# can never touch the other role's record.
# ===========================================================================
def test_ui_payload_awaiting_counter_evidence_badge(
    escrow, direct_vm, direct_alice, direct_bob
):
    """Drives the 'Awaiting Counter-Evidence' badge and the countdown timer."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    esc = escrow.get_escrow(eid)
    assert esc["dispute_response_phase"] == "AWAITING_RESPONSE"
    assert esc["dispute_awaiting_party"] == "SELLER"
    assert esc["buyer_responded"] is True
    assert esc["seller_responded"] is False
    assert esc["dispute_response_window_expired"] is False
    assert esc["can_resolve"] is False
    # Full window still on the clock, so the UI can render a live countdown.
    assert esc["dispute_response_seconds_remaining"] == WINDOW_SECONDS
    assert esc["dispute_response_deadline_ts"] == esc["dispute_raised_ts"] + WINDOW_SECONDS


def test_ui_payload_response_window_expired_badge(
    escrow, direct_vm, direct_alice, direct_bob
):
    """Drives the 'Response Window Expired' badge and enables Resolve."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.warp(WELL_AFTER)

    esc = escrow.get_escrow(eid)
    assert esc["dispute_response_phase"] == "WINDOW_EXPIRED"
    assert esc["dispute_response_window_expired"] is True
    assert esc["dispute_response_seconds_remaining"] == 0
    assert esc["can_resolve"] is True


def test_ui_both_roles_see_both_records(escrow, direct_vm, direct_alice, direct_bob):
    """Buyer and seller read the SAME two attributable records.

    ``get_escrow`` is a view with no caller-dependent branching, so both UI roles
    render identical evidence - neither side gets a filtered or one-sided view.
    """
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.raise_dispute(eid, "Buyer statement: the work was never delivered", "https://buyer.example/a")
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Seller statement: delivery was made on time", "https://seller.example/b")

    direct_vm.sender = direct_alice
    as_buyer = escrow.get_escrow(eid)
    direct_vm.sender = direct_bob
    as_seller = escrow.get_escrow(eid)

    for view in (as_buyer, as_seller):
        assert view["buyer_dispute_reason"] == "Buyer statement: the work was never delivered"
        assert view["buyer_dispute_evidence"] == "https://buyer.example/a"
        assert view["seller_dispute_reason"] == "Seller statement: delivery was made on time"
        assert view["seller_dispute_evidence"] == "https://seller.example/b"
        assert view["buyer_responded"] is True
        assert view["seller_responded"] is True

    assert as_buyer == as_seller


def test_ui_buyer_write_touches_only_the_buyer_record(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The buyer's submission may only populate the buyer's own slot."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    esc = escrow.get_escrow(eid)
    assert esc["buyer_dispute_reason"] != ""
    assert esc["buyer_dispute_evidence"] == "https://buyer.example/proof"
    # The seller's slot is untouched by the buyer's write.
    assert esc["seller_dispute_reason"] == ""
    assert esc["seller_dispute_evidence"] == ""
    assert esc["seller_dispute_at"] == ""


def test_ui_seller_write_touches_only_the_seller_record(
    escrow, direct_vm, direct_alice, direct_bob
):
    """And symmetrically for the seller - no cross-writes, so the UI can safely
    bind each form to the connected wallet's own record."""
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Seller statement filed without any buyer input", "https://seller.example/only")

    esc = escrow.get_escrow(eid)
    assert esc["seller_dispute_evidence"] == "https://seller.example/only"
    assert esc["buyer_dispute_reason"] == ""
    assert esc["buyer_dispute_evidence"] == ""
    assert esc["buyer_dispute_at"] == ""


def test_ui_neither_role_can_edit_the_other_record(
    escrow, direct_vm, direct_alice, direct_bob
):
    """Backstop for the UI's per-role form gating: even a hand-crafted call from
    the wrong wallet cannot overwrite the counterparty's statement."""
    direct_vm.warp(OPEN_AT)
    eid = _fund(direct_vm, escrow, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    escrow.raise_dispute(eid, "Buyer original statement of the problem", "https://buyer.example/original")
    direct_vm.sender = direct_bob
    escrow.raise_dispute(eid, "Seller original statement of delivery", "https://seller.example/original")

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert(
        "[EXPECTED] Buyer dispute record already exists and cannot be overwritten"
    ):
        escrow.raise_dispute(eid, "Buyer trying to rewrite the record", "https://buyer.example/tampered")

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert(
        "[EXPECTED] Seller dispute record already exists and cannot be overwritten"
    ):
        escrow.raise_dispute(eid, "Seller trying to rewrite the record", "https://seller.example/tampered")

    esc = escrow.get_escrow(eid)
    assert esc["buyer_dispute_evidence"] == "https://buyer.example/original"
    assert esc["seller_dispute_evidence"] == "https://seller.example/original"


def test_ui_outsider_cannot_file_into_either_record(
    escrow, direct_vm, direct_alice, direct_bob, direct_charlie
):
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("[EXPECTED] Only buyer or seller may raise dispute"):
        escrow.raise_dispute(eid, "Unrelated party injecting a statement", "https://evil.example")

    esc = escrow.get_escrow(eid)
    assert esc["seller_dispute_reason"] == ""
    assert esc["dispute_awaiting_party"] == "SELLER"


def test_dispute_response_status_exposes_the_window_constant(
    escrow, direct_vm, direct_alice, direct_bob
):
    """The UI reads the window length from chain rather than hardcoding 48h."""
    eid = _open_buyer_dispute(direct_vm, escrow, direct_alice, direct_bob)
    status = escrow.get_dispute_response_status(eid)

    assert status["window_seconds"] == WINDOW_SECONDS
    assert status["escrow_id"] == eid
    assert status["status"] == "DISPUTED"
    assert status["response_deadline_ts"] - status["seconds_remaining"] == (
        escrow.get_escrow(eid)["dispute_raised_ts"]
    )
