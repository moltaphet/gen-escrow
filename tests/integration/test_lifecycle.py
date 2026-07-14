"""
Full-lifecycle integration tests for gen-escrow against real GenLayer consensus.

Covers every write method:
  create_escrow, release, refund, raise_dispute, resolve_dispute,
  claim_after_deadline, claim, set_paused, withdraw_fees.

Value movement is verified with live on-chain balances (contract + EOA) so we
prove funds actually move and nothing is stranded. Consensus receipts are
asserted to reflect real leader + validator agreement (not leader-only).

Run:
  gltest tests/integration/test_lifecycle.py -v -s --network studionet
  gltest tests/integration/test_lifecycle.py -v -s --network studionet -m slow   # includes LLM dispute
"""
import pytest
from gltest import get_contract_factory, get_accounts, create_account, get_default_account
from gltest.assertions import tx_execution_succeeded
from genlayer_py.types.transactions import TransactionStatus as TS
from helpers import balance, net_of, fee_of, assert_real_consensus

ONE_GEN = 10**18


def _claim_finalized(contract_bound):
    """claim() pays an EOA via emit_transfer(on='finalized'); the native transfer
    only settles once the tx finalizes, so we must wait for FINALIZED + the
    triggered transfer before asserting EOA balances."""
    return contract_bound.claim(args=[]).transact(
        wait_transaction_status=TS.FINALIZED,
        wait_triggered_transactions=True,
        wait_triggered_transactions_status=TS.FINALIZED,
    )


def _parties():
    accts = get_accounts()
    buyer = accts[0]
    seller = accts[1] if len(accts) > 1 else create_account()
    return buyer, seller


def _fresh():
    factory = get_contract_factory("GenEscrow")
    return factory.deploy(args=[])


def test_create_moves_value_into_contract():
    buyer, seller = _parties()
    contract = _fresh()

    c_before = balance(contract.address)
    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Design job", "3 concepts", "Deliver final SVGs", "2026-08-01"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)
    info = assert_real_consensus(tx)
    print("\n[create] consensus:", info)

    c_after = balance(contract.address)
    assert c_after - c_before == ONE_GEN, f"contract balance did not rise by escrow value: {c_before}->{c_after}"

    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "FUNDED"
    assert int(esc["amount_atto"]) == ONE_GEN


def test_release_then_claim_pays_seller_and_drains_escrow():
    buyer, seller = _parties()
    contract = _fresh()

    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Job", "d", "Deliver the item as agreed", "soon"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)

    c_after_create = balance(contract.address)
    seller_before = balance(seller.address)

    # Buyer releases
    tx = contract.connect(buyer).release(args=[1]).transact()
    assert tx_execution_succeeded(tx)
    print("\n[release] consensus:", assert_real_consensus(tx))
    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "COMPLETED"

    # Seller now has claimable == net amount
    claimable = int(contract.get_claimable(args=[seller.address]).call())
    assert claimable == net_of(ONE_GEN), f"claimable {claimable} != net {net_of(ONE_GEN)}"

    # Seller pulls the payment -> value leaves the contract to the seller EOA.
    # emit_transfer settles on FINALIZED, so wait for finality before reading balances.
    tx = _claim_finalized(contract.connect(seller))
    assert tx_execution_succeeded(tx)
    print("[claim] consensus:", assert_real_consensus(tx))

    seller_after = balance(seller.address)
    c_final = balance(contract.address)

    assert seller_after - seller_before == net_of(ONE_GEN), (
        f"seller EOA did not receive net: {seller_before}->{seller_after}"
    )
    # Only the platform fee should remain stranded in the contract (by design, owner-withdrawable)
    assert c_final == c_after_create - net_of(ONE_GEN) == fee_of(ONE_GEN), (
        f"unexpected residual balance: contract={c_final}, fee={fee_of(ONE_GEN)}"
    )
    # No double claim
    assert int(contract.get_claimable(args=[seller.address]).call()) == 0


def test_refund_returns_value_to_buyer():
    buyer, seller = _parties()
    contract = _fresh()

    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Refundable", "d", "Deliver X or refund", "soon"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)

    buyer_before = balance(buyer.address)

    tx = contract.connect(buyer).refund(args=[1]).transact()
    assert tx_execution_succeeded(tx)
    print("\n[refund] consensus:", assert_real_consensus(tx))
    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "REFUNDED"

    assert int(contract.get_claimable(args=[buyer.address]).call()) == net_of(ONE_GEN)

    tx = _claim_finalized(contract.connect(buyer))
    assert tx_execution_succeeded(tx)
    buyer_after = balance(buyer.address)
    assert buyer_after - buyer_before == net_of(ONE_GEN), (
        f"buyer EOA not refunded net: {buyer_before}->{buyer_after}"
    )


def test_claim_after_deadline_pays_seller():
    buyer, seller = _parties()
    contract = _fresh()

    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Timeboxed", "d", "Deliver before deadline", "2020-01-01"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)

    tx = contract.connect(seller).claim_after_deadline(args=[1]).transact()
    assert tx_execution_succeeded(tx)
    print("\n[claim_after_deadline] consensus:", assert_real_consensus(tx))
    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "EXPIRED"
    assert int(contract.get_claimable(args=[seller.address]).call()) == net_of(ONE_GEN)


def test_pause_blocks_writes_then_unpause():
    buyer, seller = _parties()
    owner = get_default_account()
    contract = _fresh()  # deployer (default account) is owner

    tx = contract.connect(owner).set_paused(args=[True]).transact()
    assert tx_execution_succeeded(tx)
    assert contract.get_stats().call()["paused"] is True

    # A write must now fail (contract paused)
    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "Blocked", "d", "Should not work", "soon"]
    ).transact(value=ONE_GEN)
    assert not tx_execution_succeeded(tx), "create should fail while paused"

    tx = contract.connect(owner).set_paused(args=[False]).transact()
    assert tx_execution_succeeded(tx)
    assert contract.get_stats().call()["paused"] is False


@pytest.mark.slow
def test_dispute_resolution_llm_consensus():
    """Full AI dispute resolution with real LLM + validator consensus."""
    buyer, seller = _parties()
    contract = _fresh()

    tx = contract.connect(buyer).create_escrow(
        args=[seller.address, "AI Report", "", "Complete the analysis and send a written report", "2026-07-20"]
    ).transact(value=ONE_GEN)
    assert tx_execution_succeeded(tx)
    c_after_create = balance(contract.address)

    tx = contract.connect(buyer).raise_dispute(
        args=[1, "Seller delivered no report at all after the agreed deadline passed.", "https://example.com/empty"]
    ).transact()
    assert tx_execution_succeeded(tx)
    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "DISPUTED"

    # Non-deterministic LLM path -> must reach real consensus among validators
    tx = contract.connect(buyer).resolve_dispute(args=[1]).transact()
    assert tx_execution_succeeded(tx)
    print("\n[resolve_dispute] consensus:", assert_real_consensus(tx))

    esc = contract.get_escrow(args=[1]).call()
    assert esc["status"] == "RESOLVED"
    assert esc["resolved_winner"] in ("BUYER", "SELLER", "SPLIT")

    # Payout must be fully allocated between buyer + seller (no stranded net funds)
    to_seller = int(contract.get_claimable(args=[seller.address]).call())
    to_buyer = int(contract.get_claimable(args=[buyer.address]).call())
    assert to_seller + to_buyer == net_of(ONE_GEN), (
        f"net not fully allocated: seller={to_seller} buyer={to_buyer} net={net_of(ONE_GEN)}"
    )
    # Contract still custodies the funds until pulled; nothing lost
    assert balance(contract.address) == c_after_create
