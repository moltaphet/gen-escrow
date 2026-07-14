"""Direct-mode tests for GenEscrow (fast, leader-only).

In direct mode:
- Call contract methods directly (no .args= or .transact())
- Writes return values directly
- Views return values directly
- Use direct_vm.sender and direct_vm.value for context
- Use direct_vm.expect_revert(...) for expected failures

Note: direct_* account fixtures are provided as raw bytes. We convert
them to 0x-hex strings when passing to contract methods.
"""

def _addr(account) -> str:
    """Convert direct-mode account (bytes) or Address to 0x-hex string."""
    if isinstance(account, (bytes, bytearray)):
        return "0x" + account.hex()
    if hasattr(account, "hex"):
        return "0x" + account.hex()
    s = str(account)
    if s.startswith("0x"):
        return s
    # fallback
    return "0x" + bytes.fromhex(s.replace("0x", "")).hex() if len(s) > 10 else s


def test_create_and_fund(direct_vm, direct_deploy, direct_alice, direct_bob, contract_path):
    """Buyer creates and funds an escrow in one step."""
    contract = direct_deploy(contract_path)

    # Set up the transaction context
    direct_vm.sender = direct_alice
    direct_vm.value = 10**18   # 1 GEN in wei/atto

    seller_addr = _addr(direct_bob)

    # Direct call — returns the escrow id immediately
    eid = contract.create_escrow(
        seller_addr,
        "Logo design project",
        "Design 3 concepts + final files",
        "Release on delivery of final AI + SVG exports",
        "2026-08-01",
    )

    assert eid == 1

    # View calls return dicts directly (no .call())
    esc = contract.get_escrow(eid)
    # Note: contract Address str() produces checksummed (mixed-case) addresses.
    # We normalize for comparison.
    assert esc["buyer"].lower() == _addr(direct_alice).lower()
    assert esc["seller"].lower() == seller_addr.lower()
    assert esc["status"] == "FUNDED"
    assert int(esc["amount_atto"]) == 10**18


def test_release_happy_path(direct_vm, direct_deploy, direct_alice, direct_bob, contract_path):
    contract = direct_deploy(contract_path)

    direct_vm.sender = direct_alice
    direct_vm.value = 2 * 10**18

    seller_addr = _addr(direct_bob)
    eid = contract.create_escrow(
        seller_addr, "Work", "Desc", "Deliver X", "soon"
    )

    # Buyer releases (direct call)
    direct_vm.sender = direct_alice
    contract.release(eid)

    esc = contract.get_escrow(eid)
    assert esc["status"] == "COMPLETED"
    assert int(esc["released_to_seller_atto"]) > 0

    # Seller should now have claimable balance
    claimable = contract.get_claimable(seller_addr)
    assert int(claimable) > 0


def test_dispute_and_state(direct_vm, direct_deploy, direct_alice, direct_bob, contract_path):
    contract = direct_deploy(contract_path)

    direct_vm.sender = direct_alice
    direct_vm.value = 10**18

    seller_addr = _addr(direct_bob)
    eid = contract.create_escrow(
        seller_addr, "Job", "", "Do the work", "later"
    )

    # Raise dispute as seller
    direct_vm.sender = direct_bob
    contract.raise_dispute(eid, "Buyer never responded to messages", "https://example.com/chat")

    esc = contract.get_escrow(eid)
    assert esc["status"] == "DISPUTED"
    assert "Buyer never responded" in esc["dispute_reason"]


def test_only_buyer_can_release(direct_vm, direct_deploy, direct_alice, direct_bob, contract_path):
    contract = direct_deploy(contract_path)

    direct_vm.sender = direct_alice
    direct_vm.value = 10**18

    seller_addr = _addr(direct_bob)
    eid = contract.create_escrow(
        seller_addr, "Fix", "Y", "Deliver the item as agreed", "t"
    )

    # Seller tries to release — should revert.
    # Note: the actual error includes the ERROR_EXPECTED prefix.
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only buyer can release"):
        contract.release(eid)
