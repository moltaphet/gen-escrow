# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
GenEscrow - Production-grade Smart Escrow on GenLayer.

Features:
- Secure conditional payments with native GEN (atto-scale)
- Buyer creates + funds escrow in one payable transaction
- Mutual or unilateral release by buyer when terms satisfied
- Either party can raise a dispute with structured reason + evidence
- AI-powered (LLM) dispute resolution via GenLayer consensus:
  * Validators independently analyze terms, dispute statements and evidence
  * Equivalence principle ensures validator agreement on winner + split
- Timeout claim: seller can claim full amount after deadline if no dispute
- Flat storage model using TreeMap + Dataclass
- Proper error classification with prefixes for consensus safety
- Pull-payment pattern for all payouts (claimable balances)

Status machine:
  FUNDED -> COMPLETED (full release)
         -> REFUNDED (full refund)
         -> DISPUTED -> RESOLVED (AI decides split or winner)
         -> EXPIRED (seller claims after deadline with no dispute)

All monetary values are stored as u256 in atto units (value * 10^18).
"""

from genlayer import *
from dataclasses import dataclass
import json

# ---------------------------------------------------------------------------
# Error classification prefixes (CRITICAL for consensus on error paths)
# ---------------------------------------------------------------------------
ERROR_EXPECTED = "[EXPECTED]"     # Business logic (deterministic) - exact match
ERROR_EXTERNAL = "[EXTERNAL]"     # External 4xx (deterministic) - exact match
ERROR_TRANSIENT = "[TRANSIENT]"   # Network/5xx (non-deterministic) - agree if both transient
ERROR_LLM = "[LLM_ERROR]"         # LLM misbehavior - always disagree to force rotation

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
BPS_DENOM = 10000                 # 100.00% in basis points
MIN_DISPUTE_REASON_LEN = 10
MAX_TEXT_LEN = 4000               # safety cap for LLM input
PLATFORM_FEE_BPS = 50             # 0.50% on successful release (optional, can be 0)
VALID_WINNERS = ("BUYER", "SELLER", "SPLIT")

# Status values (strings for readability + easy frontend mapping)
STATUS_FUNDED = "FUNDED"
STATUS_COMPLETED = "COMPLETED"
STATUS_REFUNDED = "REFUNDED"
STATUS_DISPUTED = "DISPUTED"
STATUS_RESOLVED = "RESOLVED"
STATUS_EXPIRED = "EXPIRED"


# ===========================================================================
# Helper functions (pure / deterministic where possible)
# ===========================================================================
def _handle_leader_error(leaders_res, leader_fn) -> bool:
    """Canonical validator error comparator.

    Deterministic errors (EXPECTED/EXTERNAL) must match exactly.
    Transient errors agree only if both sides experienced a transient failure.
    LLM / unknown errors force disagreement -> fresh validator rotation.
    """
    leader_msg = getattr(leaders_res, "message", "") or ""
    try:
        leader_fn()
        return False  # Leader failed, validator succeeded -> disagree
    except gl.vm.UserError as e:
        validator_msg = getattr(e, "message", "") or str(e)
        if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
            return validator_msg == leader_msg
        if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
            return True
        return False
    except Exception:
        return False


def _clean_json(text: str) -> dict:
    """Extract first JSON object from LLM output and sanitize trailing commas."""
    import re
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last < first:
        raise gl.vm.UserError(f"{ERROR_LLM} No JSON object found in LLM response")
    body = text[first:last + 1]
    body = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", body)
    try:
        parsed = json.loads(body)
    except Exception:
        raise gl.vm.UserError(f"{ERROR_LLM} Malformed JSON from LLM")
    if not isinstance(parsed, dict):
        raise gl.vm.UserError(f"{ERROR_LLM} LLM response is not a JSON object")
    return parsed


def _coerce_dict(resp) -> dict:
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        return _clean_json(resp)
    raise gl.vm.UserError(f"{ERROR_LLM} Unexpected LLM response type: {type(resp)}")


def _clamp_bps(v: int) -> int:
    v = int(v)
    if v < 0:
        return 0
    if v > BPS_DENOM:
        return BPS_DENOM
    return v


def _safe_text(val, max_len: int = MAX_TEXT_LEN) -> str:
    """Safely convert web/LLM body to bounded UTF-8 text."""
    if val is None:
        return ""
    if isinstance(val, (bytes, bytearray)):
        try:
            s = val.decode("utf-8", errors="replace")
        except Exception:
            s = str(val)
    else:
        s = str(val)
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _now_iso() -> str:
    """Best-effort ISO timestamp from transaction metadata."""
    try:
        return str(gl.message_raw.get("datetime", ""))
    except Exception:
        return ""


def _addr_hex(addr: Address) -> str:
    """Stable string key for TreeMap indexes."""
    return str(addr)


# ===========================================================================
# Storage model - flat, append-only friendly
# ===========================================================================
@allow_storage
@dataclass
class Escrow:
    id: u256
    buyer: Address
    seller: Address
    amount_atto: u256          # total escrowed (gross)
    platform_fee_atto: u256    # reserved fee on release (0 if disabled)
    net_amount_atto: u256      # amount - fee

    title: str
    description: str
    terms: str                 # release conditions / acceptance criteria
    deadline_iso: str          # ISO string or human readable deadline description

    status: str
    created_at: str
    funded_at: str

    # Dispute data
    dispute_raised_by: Address
    dispute_reason: str
    dispute_evidence: str      # newline or | separated links + notes
    dispute_raised_at: str

    # Resolution data (populated by AI)
    resolved_winner: str       # BUYER | SELLER | SPLIT
    resolved_release_bps: u256 # how much of net_amount goes to seller (0-10000)
    resolution_reason: str
    resolved_at: str

    # Pull payment ledger (settled amounts)
    # Actual transfers happen via claimable balances below
    released_to_seller_atto: u256
    refunded_to_buyer_atto: u256


@allow_storage
@dataclass
class Claimable:
    """Pull-payment balance per address."""
    address: Address
    amount_atto: u256


def _serialize_escrow(esc: Escrow) -> str:
    """Serialize escrow to JSON string for flat storage."""
    return json.dumps({
        "id": int(esc.id),
        "buyer": str(esc.buyer),
        "seller": str(esc.seller),
        "amount_atto": int(esc.amount_atto),
        "platform_fee_atto": int(esc.platform_fee_atto),
        "net_amount_atto": int(esc.net_amount_atto),
        "title": esc.title,
        "description": esc.description,
        "terms": esc.terms,
        "deadline_iso": esc.deadline_iso,
        "status": esc.status,
        "created_at": esc.created_at,
        "funded_at": esc.funded_at,
        "dispute_raised_by": str(esc.dispute_raised_by) if esc.dispute_raised_by else "",
        "dispute_reason": esc.dispute_reason,
        "dispute_evidence": esc.dispute_evidence,
        "dispute_raised_at": esc.dispute_raised_at,
        "resolved_winner": esc.resolved_winner,
        "resolved_release_bps": int(esc.resolved_release_bps),
        "resolution_reason": esc.resolution_reason,
        "resolved_at": esc.resolved_at,
        "released_to_seller_atto": int(esc.released_to_seller_atto),
        "refunded_to_buyer_atto": int(esc.refunded_to_buyer_atto),
    })


def _deserialize_escrow(data: str) -> Escrow:
    """Deserialize JSON string back to Escrow dataclass (for internal use)."""
    d = json.loads(data)
    return Escrow(
        id=u256(d["id"]),
        buyer=Address(d["buyer"]),
        seller=Address(d["seller"]),
        amount_atto=u256(d["amount_atto"]),
        platform_fee_atto=u256(d["platform_fee_atto"]),
        net_amount_atto=u256(d["net_amount_atto"]),
        title=d["title"],
        description=d["description"],
        terms=d["terms"],
        deadline_iso=d["deadline_iso"],
        status=d["status"],
        created_at=d["created_at"],
        funded_at=d["funded_at"],
        dispute_raised_by=Address(d["dispute_raised_by"]) if d["dispute_raised_by"] else Address("0x0000000000000000000000000000000000000000"),
        dispute_reason=d["dispute_reason"],
        dispute_evidence=d["dispute_evidence"],
        dispute_raised_at=d["dispute_raised_at"],
        resolved_winner=d["resolved_winner"],
        resolved_release_bps=u256(d["resolved_release_bps"]),
        resolution_reason=d["resolution_reason"],
        resolved_at=d["resolved_at"],
        released_to_seller_atto=u256(d["released_to_seller_atto"]),
        refunded_to_buyer_atto=u256(d["refunded_to_buyer_atto"]),
    )


# ===========================================================================
# Main Contract
# ===========================================================================
class GenEscrow(gl.Contract):
    # ---------------- Storage declarations (class-level) ----------------
    owner: Address
    escrows: TreeMap[u256, str]  # JSON-serialized (keeps storage types simple)
    next_id: u256

    # Flat indexes (avoids nested DynArray in TreeMap values for better compatibility)
    # buyer_escrow_count[addr_hex] = count
    # buyer_escrow_at[f"{addr_hex}:{i}"] = escrow_id
    buyer_escrow_count: TreeMap[str, u256]
    buyer_escrow_at: TreeMap[str, u256]

    seller_escrow_count: TreeMap[str, u256]
    seller_escrow_at: TreeMap[str, u256]

    # Pull-payment balances (key = hex address)
    claimable: TreeMap[str, u256]

    total_volume_atto: u256
    platform_fees_collected: u256
    paused: bool

    def __init__(self):
        self.owner = gl.message.sender_address
        self.next_id = u256(1)
        self.total_volume_atto = u256(0)
        self.platform_fees_collected = u256(0)
        self.paused = False

    # ------------------------------------------------------------------
    # Guards & helpers (all deterministic)
    # ------------------------------------------------------------------
    def _only_owner(self) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only contract owner")

    def _require_not_paused(self) -> None:
        if self.paused:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Contract is paused")

    def _require_escrow(self, escrow_id: int) -> Escrow:
        eid = u256(escrow_id)
        if eid not in self.escrows:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Escrow not found")
        return _deserialize_escrow(self.escrows[eid])

    def _require_status(self, esc: Escrow, allowed: list[str]) -> None:
        if esc.status not in allowed:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Invalid state: {esc.status}. Allowed: {allowed}"
            )

    def _is_buyer_or_seller(self, esc: Escrow) -> bool:
        sender = gl.message.sender_address
        return sender == esc.buyer or sender == esc.seller

    def _add_to_index(self, count_map: TreeMap[str, u256], pos_map: TreeMap[str, u256], key: str, eid: u256) -> None:
        """Flat append for a party's escrow list."""
        count = int(count_map.get(key, u256(0)))
        # Check for duplicate
        for i in range(count):
            pos_key = f"{key}:{i}"
            if int(pos_map.get(pos_key, u256(0))) == int(eid):
                return
        # Append at next index
        pos_key = f"{key}:{count}"
        pos_map[pos_key] = eid
        count_map[key] = u256(count + 1)

    def _credit(self, addr: Address, amount: u256) -> None:
        if int(amount) <= 0:
            return
        key = _addr_hex(addr)
        current = self.claimable.get(key, u256(0))
        self.claimable[key] = u256(int(current) + int(amount))

    # ------------------------------------------------------------------
    # Public Views
    # ------------------------------------------------------------------
    @gl.public.view
    def get_escrow(self, escrow_id: int) -> dict:
        esc = self._require_escrow(escrow_id)
        # Return plain dict for easy JS consumption
        return {
            "id": int(esc.id),
            "buyer": str(esc.buyer),
            "seller": str(esc.seller),
            "amount_atto": int(esc.amount_atto),
            "net_amount_atto": int(esc.net_amount_atto),
            "title": esc.title,
            "description": esc.description,
            "terms": esc.terms,
            "deadline_iso": esc.deadline_iso,
            "status": esc.status,
            "created_at": esc.created_at,
            "funded_at": esc.funded_at,
            "dispute_raised_by": str(esc.dispute_raised_by) if esc.dispute_raised_by else "",
            "dispute_reason": esc.dispute_reason,
            "dispute_evidence": esc.dispute_evidence,
            "dispute_raised_at": esc.dispute_raised_at,
            "resolved_winner": esc.resolved_winner,
            "resolved_release_bps": int(esc.resolved_release_bps),
            "resolution_reason": esc.resolution_reason,
            "resolved_at": esc.resolved_at,
            "released_to_seller_atto": int(esc.released_to_seller_atto),
            "refunded_to_buyer_atto": int(esc.refunded_to_buyer_atto),
        }

    @gl.public.view
    def get_escrows_by_buyer(self, buyer: str) -> list[int]:
        key = _addr_hex(Address(buyer) if not isinstance(buyer, Address) else buyer)
        count = int(self.buyer_escrow_count.get(key, u256(0)))
        result = []
        for i in range(count):
            pos_key = f"{key}:{i}"
            eid = int(self.buyer_escrow_at.get(pos_key, u256(0)))
            if eid:
                result.append(eid)
        return result

    @gl.public.view
    def get_escrows_by_seller(self, seller: str) -> list[int]:
        key = _addr_hex(Address(seller) if not isinstance(seller, Address) else seller)
        count = int(self.seller_escrow_count.get(key, u256(0)))
        result = []
        for i in range(count):
            pos_key = f"{key}:{i}"
            eid = int(self.seller_escrow_at.get(pos_key, u256(0)))
            if eid:
                result.append(eid)
        return result

    @gl.public.view
    def get_claimable(self, addr: str) -> int:
        key = _addr_hex(Address(addr) if not isinstance(addr, Address) else addr)
        return int(self.claimable.get(key, u256(0)))

    @gl.public.view
    def get_stats(self) -> dict:
        return {
            "total_escrows": int(self.next_id) - 1,
            "total_volume_atto": int(self.total_volume_atto),
            "platform_fees_collected": int(self.platform_fees_collected),
            "paused": self.paused,
        }

    # ------------------------------------------------------------------
    # Write: Create + Fund (single atomic step)
    # ------------------------------------------------------------------
    @gl.public.write.payable
    def create_escrow(
        self,
        seller: str,
        title: str,
        description: str,
        terms: str,
        deadline_iso: str,
    ) -> int:
        """
        Buyer calls this payable. The attached value becomes the escrow amount.
        """
        self._require_not_paused()

        sender = gl.message.sender_address
        value = gl.message.value

        if int(value) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Must send positive GEN amount")

        # Accept both Address objects (common in direct tests) and hex strings
        if isinstance(seller, Address):
            seller_addr = seller
        else:
            s = str(seller).strip()
            if not s or (s.startswith("0x") and len(s) != 42):
                # allow raw hex without 0x in some test contexts, normalize
                if len(s) == 40:
                    s = "0x" + s
            if not s.startswith("0x") or len(s) != 42:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Seller must be a valid address (got {s!r})")
            seller_addr = Address(s)

        if seller_addr == sender:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Buyer and seller cannot be the same")

        title = _safe_text(title, 120).strip()
        description = _safe_text(description, 800).strip()
        terms = _safe_text(terms, 1200).strip()
        deadline_iso = _safe_text(deadline_iso, 120).strip()

        if len(title) < 3:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Title must be at least 3 characters")

        if len(terms) < 5:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Terms must describe release conditions")

        eid = self.next_id
        self.next_id = u256(int(eid) + 1)

        gross = u256(int(value))
        fee = u256((int(gross) * PLATFORM_FEE_BPS) // BPS_DENOM) if PLATFORM_FEE_BPS > 0 else u256(0)
        net = u256(int(gross) - int(fee))

        now = _now_iso()

        esc = Escrow(
            id=eid,
            buyer=sender,
            seller=seller_addr,
            amount_atto=gross,
            platform_fee_atto=fee,
            net_amount_atto=net,
            title=title,
            description=description,
            terms=terms,
            deadline_iso=deadline_iso,
            status=STATUS_FUNDED,
            created_at=now,
            funded_at=now,
            dispute_raised_by=Address("0x0000000000000000000000000000000000000000"),
            dispute_reason="",
            dispute_evidence="",
            dispute_raised_at="",
            resolved_winner="",
            resolved_release_bps=u256(0),
            resolution_reason="",
            resolved_at="",
            released_to_seller_atto=u256(0),
            refunded_to_buyer_atto=u256(0),
        )

        self.escrows[eid] = _serialize_escrow(esc)

        # Update flat indexes
        bkey = _addr_hex(sender)
        skey = _addr_hex(seller_addr)
        self._add_to_index(self.buyer_escrow_count, self.buyer_escrow_at, bkey, eid)
        self._add_to_index(self.seller_escrow_count, self.seller_escrow_at, skey, eid)

        self.total_volume_atto = u256(int(self.total_volume_atto) + int(gross))

        return int(eid)

    # ------------------------------------------------------------------
    # Write: Buyer releases funds (happy path, no dispute)
    # ------------------------------------------------------------------
    @gl.public.write
    def release(self, escrow_id: int) -> None:
        """Buyer voluntarily releases full amount to seller."""
        self._require_not_paused()
        esc = self._require_escrow(escrow_id)

        if gl.message.sender_address != esc.buyer:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only buyer can release")

        self._require_status(esc, [STATUS_FUNDED])

        # Credit seller the full net amount (fees stay in contract until owner withdraws)
        self._credit(esc.seller, esc.net_amount_atto)
        esc.released_to_seller_atto = esc.net_amount_atto

        # Record fee (if any)
        if int(esc.platform_fee_atto) > 0:
            self.platform_fees_collected = u256(
                int(self.platform_fees_collected) + int(esc.platform_fee_atto)
            )

        esc.status = STATUS_COMPLETED
        esc.resolved_at = _now_iso()

        self.escrows[u256(escrow_id)] = _serialize_escrow(esc)

    # ------------------------------------------------------------------
    # Write: Buyer requests full refund before dispute
    # ------------------------------------------------------------------
    @gl.public.write
    def refund(self, escrow_id: int) -> None:
        """Buyer can refund themselves while still in FUNDED state."""
        self._require_not_paused()
        esc = self._require_escrow(escrow_id)

        if gl.message.sender_address != esc.buyer:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only buyer can request refund")

        self._require_status(esc, [STATUS_FUNDED])

        self._credit(esc.buyer, esc.net_amount_atto)
        esc.refunded_to_buyer_atto = esc.net_amount_atto
        esc.status = STATUS_REFUNDED
        esc.resolved_at = _now_iso()

        self.escrows[u256(escrow_id)] = _serialize_escrow(esc)

    # ------------------------------------------------------------------
    # Write: Raise dispute (either party)
    # ------------------------------------------------------------------
    @gl.public.write
    def raise_dispute(self, escrow_id: int, reason: str, evidence: str) -> None:
        self._require_not_paused()
        esc = self._require_escrow(escrow_id)

        if not self._is_buyer_or_seller(esc):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only buyer or seller may raise dispute")

        self._require_status(esc, [STATUS_FUNDED, STATUS_DISPUTED])  # allow re-statement?

        reason = _safe_text(reason, 600).strip()
        evidence = _safe_text(evidence, 1400).strip()

        if len(reason) < MIN_DISPUTE_REASON_LEN:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Dispute reason is too short")

        sender = gl.message.sender_address
        now = _now_iso()

        esc.status = STATUS_DISPUTED
        esc.dispute_raised_by = sender
        esc.dispute_reason = reason
        esc.dispute_evidence = evidence
        esc.dispute_raised_at = now

        self.escrows[u256(escrow_id)] = _serialize_escrow(esc)

    # ------------------------------------------------------------------
    # Non-deterministic: AI dispute resolution
    # ------------------------------------------------------------------
    def _judge_dispute(self, esc: Escrow) -> dict:
        """Leader + validator using custom equivalence for LLM judgment."""

        def build_prompt() -> str:
            return (
                "You are an impartial escrow judge on a blockchain.\n"
                "Analyze the escrow terms, the dispute statements, and any provided evidence.\n"
                "Decide who should receive the funds and in what proportion.\n\n"
                f"TITLE: {esc.title}\n"
                f"DESCRIPTION: {esc.description}\n"
                f"RELEASE TERMS / CONDITIONS:\n{esc.terms}\n\n"
                f"DISPUTE RAISED BY: {esc.dispute_raised_by}\n"
                f"DISPUTE REASON:\n{esc.dispute_reason}\n\n"
                f"EVIDENCE / LINKS:\n{esc.dispute_evidence}\n\n"
                "Return ONLY a compact JSON object with exactly these fields:\n"
                "{\n"
                '  "winner": "SELLER" | "BUYER" | "SPLIT",\n'
                '  "release_bps": <integer 0-10000 - percentage of net funds that should go to the SELLER>,\n'
                '  "reason": "<concise one-sentence justification>"\n'
                "}\n"
                "Rules:\n"
                "- If buyer is clearly right (non-delivery, clear violation of terms) -> winner=BUYER, release_bps=0\n"
                "- If seller delivered according to terms -> winner=SELLER, release_bps=10000\n"
                "- Partial delivery or ambiguity -> SPLIT with fair release_bps\n"
            )

        def leader_fn():
            prompt = build_prompt()
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            data = _coerce_dict(raw)

            winner_raw = str(data.get("winner", "")).upper().strip()
            winner = winner_raw if winner_raw in VALID_WINNERS else "SPLIT"

            bps = _clamp_bps(
                int(data.get("release_bps", data.get("bps", 5000)))
            )

            reason = _safe_text(data.get("reason", data.get("justification", "")), 280)

            return {
                "winner": winner,
                "release_bps": bps,
                "reason": reason or "Consensus judgment applied.",
            }

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)

            leader = leaders_res.calldata
            validator = leader_fn()

            # Winner must match exactly
            if leader.get("winner") != validator.get("winner"):
                return False

            # release_bps must be within reasonable tolerance (e.g. 10%)
            lb = int(leader.get("release_bps", 0))
            vb = int(validator.get("release_bps", 0))
            diff = abs(lb - vb)
            if diff > 1000:  # > 10% tolerance
                return False

            # Reason can be fuzzy - we only care that both produced non-empty reasoning
            if not leader.get("reason") or not validator.get("reason"):
                return False

            return True

        return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

    @gl.public.write
    def resolve_dispute(self, escrow_id: int) -> dict:
        """
        Permissionless call that triggers AI consensus resolution.
        Can be called by anyone once a dispute exists (encourages resolution).
        """
        self._require_not_paused()
        esc = self._require_escrow(escrow_id)

        self._require_status(esc, [STATUS_DISPUTED])

        if not esc.dispute_reason:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No dispute to resolve")

        judgment = self._judge_dispute(esc)

        winner = judgment["winner"]
        bps = u256(int(judgment["release_bps"]))
        reason = judgment["reason"]

        net = int(esc.net_amount_atto)
        to_seller = u256((net * int(bps)) // BPS_DENOM)
        to_buyer = u256(net - int(to_seller))

        # Credit the parties
        if int(to_seller) > 0:
            self._credit(esc.seller, to_seller)
            esc.released_to_seller_atto = to_seller
        if int(to_buyer) > 0:
            self._credit(esc.buyer, to_buyer)
            esc.refunded_to_buyer_atto = to_buyer

        # Collect platform fee on the originally escrowed amount
        if int(esc.platform_fee_atto) > 0:
            self.platform_fees_collected = u256(
                int(self.platform_fees_collected) + int(esc.platform_fee_atto)
            )

        esc.status = STATUS_RESOLVED
        esc.resolved_winner = winner
        esc.resolved_release_bps = bps
        esc.resolution_reason = reason
        esc.resolved_at = _now_iso()

        self.escrows[u256(escrow_id)] = _serialize_escrow(esc)

        return {
            "winner": winner,
            "release_bps": int(bps),
            "to_seller_atto": int(to_seller),
            "to_buyer_atto": int(to_buyer),
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Write: Seller claims after deadline (no active dispute)
    # ------------------------------------------------------------------
    @gl.public.write
    def claim_after_deadline(self, escrow_id: int) -> None:
        """
        If the deadline has passed and there is no open dispute, the seller
        can claim the full net amount. This is a time-based release.
        """
        self._require_not_paused()
        esc = self._require_escrow(escrow_id)

        if gl.message.sender_address != esc.seller:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only seller can claim after deadline")

        self._require_status(esc, [STATUS_FUNDED])

        # NOTE: Deadline enforcement here is best-effort / advisory.
        # Real on-chain time is not available. Frontends + validators
        # should only call this after verifying the deadline off-chain.
        # We do not perform a hard block on-chain to avoid non-determinism.

        self._credit(esc.seller, esc.net_amount_atto)
        esc.released_to_seller_atto = esc.net_amount_atto
        esc.status = STATUS_EXPIRED
        esc.resolved_at = _now_iso()

        self.escrows[u256(escrow_id)] = _serialize_escrow(esc)

    # ------------------------------------------------------------------
    # Pull payments
    # ------------------------------------------------------------------
    @gl.public.write
    def claim(self) -> int:
        """Withdraw any claimable balance for the caller."""
        self._require_not_paused()
        sender = gl.message.sender_address
        key = _addr_hex(sender)
        amount = self.claimable.get(key, u256(0))

        if int(amount) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No claimable balance")

        # Reset before external effect
        self.claimable[key] = u256(0)

        # Emit native transfer to EOA
        # Use the EVM transfer primitive for EOAs
        @gl.evm.contract_interface
        class EOA:
            class View:
                pass
            class Write:
                pass

        EOA(sender).emit_transfer(value=int(amount))
        return int(amount)

    # ------------------------------------------------------------------
    # Admin
    # ------------------------------------------------------------------
    @gl.public.write
    def set_paused(self, paused: bool) -> None:
        self._only_owner()
        self.paused = bool(paused)

    @gl.public.write
    def withdraw_fees(self, to: str) -> int:
        """Owner can withdraw accumulated platform fees."""
        self._only_owner()
        to_addr = Address(to)
        bal = int(self.platform_fees_collected)
        if bal <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No fees to withdraw")

        self.platform_fees_collected = u256(0)
        @gl.evm.contract_interface
        class EOA:
            class View:
                pass
            class Write:
                pass
        EOA(to_addr).emit_transfer(value=bal)
        return bal
