"""Shared helpers for gen-escrow integration tests (real consensus on StudioNet)."""
from gltest import get_gl_client

FEE_BPS = 50
BPS_DENOM = 10000


def balance(addr) -> int:
    """Native GEN balance (atto) for an address, via the live RPC."""
    return int(get_gl_client().get_balance(addr))


def net_of(gross: int) -> int:
    fee = (gross * FEE_BPS) // BPS_DENOM
    return gross - fee


def fee_of(gross: int) -> int:
    return (gross * FEE_BPS) // BPS_DENOM


def assert_real_consensus(tx: dict, min_participants: int = 2):
    """Assert the receipt reflects real leader+validator consensus, not leader-only.

    Verifies:
      - leader_only flag is False
      - a leader (last_leader / activator) exists
      - multiple validators cast votes in consensus_data
    """
    assert tx.get("leader_only") is False, f"expected full consensus, got leader_only={tx.get('leader_only')}"
    votes = (tx.get("consensus_data") or {}).get("votes", {})
    assert isinstance(votes, dict) and len(votes) >= min_participants, (
        f"expected >= {min_participants} validator votes, got {votes}"
    )
    agree = [v for v in votes.values() if v == "agree"]
    assert len(agree) >= 1, f"expected at least one 'agree' vote, got {votes}"
    assert tx.get("last_leader"), "no leader recorded in receipt"
    return {
        "tx_hash": tx.get("hash"),
        "result_name": tx.get("result_name"),
        "num_validators": len(votes),
        "agree_votes": len(agree),
        "leader": tx.get("last_leader"),
    }
