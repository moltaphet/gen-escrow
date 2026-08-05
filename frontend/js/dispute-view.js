/* ============================================================
   dispute-view.js — pure presentation logic for the dispute panel

   Deliberately dependency-free (no DOM, no SDK, no imports) so the
   per-role rules below can be unit tested directly:

       cd frontend && npm test

   Everything here is derived from the `get_escrow` payload, which carries
   the contract's own response-window state (dispute_response_phase,
   dispute_response_seconds_remaining, can_resolve, ...). The UI never
   recomputes the 48h window itself: the chain is the source of truth and
   this module only formats it.
   ============================================================ */

export const ROLE_BUYER = "BUYER";
export const ROLE_SELLER = "SELLER";
export const ROLE_OBSERVER = "OBSERVER";

function norm(a) {
  return String(a ?? "").trim().toLowerCase();
}

/** Which side of the escrow the connected wallet is on. */
export function partyRole(esc, account) {
  const a = norm(account);
  if (!a) return ROLE_OBSERVER;
  if (norm(esc?.buyer) === a) return ROLE_BUYER;
  if (norm(esc?.seller) === a) return ROLE_SELLER;
  return ROLE_OBSERVER;
}

/** Compact human countdown: 172800 -> "2d 0h", 3660 -> "1h 1m". */
export function formatCountdown(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  if (s === 0) return "0m";
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${s}s`;
}

/**
 * Status badge for the response window.
 *
 * Returns null when the escrow is not in a dispute, so callers can render
 * nothing without special-casing.
 */
export function disputeBadge(esc) {
  if (!esc || esc.status !== "DISPUTED") return null;

  const phase = esc.dispute_response_phase || "NONE";
  const remaining = esc.dispute_response_seconds_remaining;

  switch (phase) {
    case "AWAITING_RESPONSE":
      return {
        phase,
        tone: "await",
        label: `Awaiting Counter-Evidence — ${formatCountdown(remaining)} left`,
        detail:
          `The ${esc.dispute_awaiting_party === ROLE_BUYER ? "buyer" : "seller"} still has ` +
          `${formatCountdown(remaining)} to file their own statement. AI resolution is locked until then.`,
      };
    case "WINDOW_EXPIRED":
      return {
        phase,
        tone: "expired",
        label: "Response Window Expired",
        detail:
          "The counterparty did not respond within the 48h window. " +
          "The dispute can now be resolved on the record as filed.",
      };
    case "RESPONSE_WAIVED":
      return {
        phase,
        tone: "waived",
        label: "Response Waived",
        detail:
          "The non-responding party explicitly waived their reply, so resolution is unlocked early.",
      };
    case "BOTH_SUBMITTED":
      return {
        phase,
        tone: "ready",
        label: "Both Statements Filed",
        detail: "Both parties are on the record. The dispute is ready for AI resolution.",
      };
    default:
      return { phase, tone: "await", label: "Dispute Open", detail: "" };
  }
}

/**
 * The two attributable evidence records, always returned as a pair so the UI
 * shows BOTH sides — including the one that has not filed yet, rendered as an
 * explicit "no statement filed" placeholder rather than being hidden.
 */
export function disputeRecords(esc, account) {
  const role = partyRole(esc, account);
  const awaiting = esc?.dispute_awaiting_party || "";

  const build = (party, label, reason, evidence, filedAt) => ({
    party,
    label,
    statement: reason || "",
    evidence: evidence || "",
    filedAt: filedAt || "",
    filed: Boolean(filedAt),
    isYou: role === party,
    awaiting: !filedAt && awaiting === party,
  });

  return [
    build(
      ROLE_BUYER,
      "Buyer Statement & Evidence",
      esc?.buyer_dispute_reason,
      esc?.buyer_dispute_evidence,
      esc?.buyer_dispute_at,
    ),
    build(
      ROLE_SELLER,
      "Seller Statement & Evidence",
      esc?.seller_dispute_reason,
      esc?.seller_dispute_evidence,
      esc?.seller_dispute_at,
    ),
  ];
}

/**
 * Whether the connected wallet may OPEN a dispute on this escrow.
 *
 * The contract restricts raise_dispute() to the buyer and the seller, and only
 * from FUNDED / DELIVERY_SUBMITTED. Mirrored here so an observer is never shown
 * a "Raise Dispute" button that would revert on-chain.
 */
export function canOpenDispute(esc, account) {
  const role = partyRole(esc, account);
  const isParty = role === ROLE_BUYER || role === ROLE_SELLER;
  return isParty && (esc?.status === "FUNDED" || esc?.status === "DELIVERY_SUBMITTED");
}

/**
 * What the connected wallet is actually allowed to do in this dispute.
 *
 * The contract enforces all of this; the UI mirrors it so a party is never
 * offered a button that would revert. Crucially, a party may only ever write
 * their OWN record, and only while that record is empty (records are
 * write-once on chain).
 */
export function disputeCapabilities(esc, account) {
  const role = partyRole(esc, account);
  const isParty = role === ROLE_BUYER || role === ROLE_SELLER;
  const isDisputed = esc?.status === "DISPUTED";

  const buyerFiled = Boolean(esc?.buyer_dispute_at);
  const sellerFiled = Boolean(esc?.seller_dispute_at);
  const myRecordFiled =
    role === ROLE_BUYER ? buyerFiled : role === ROLE_SELLER ? sellerFiled : false;

  const waived = Boolean(esc?.dispute_response_waived);
  const canResolve = Boolean(esc?.can_resolve);

  let resolveBlockedReason = "";
  if (isDisputed && !canResolve) {
    const who = esc?.dispute_awaiting_party === ROLE_BUYER ? "buyer" : "seller";
    resolveBlockedReason =
      `Locked until the ${who} responds, waives their reply, or the window expires ` +
      `(${formatCountdown(esc?.dispute_response_seconds_remaining)} left).`;
  }

  return {
    role,
    isParty,
    myRecordFiled,
    // Submit is offered only to the connected party, only for their own slot.
    canFileStatement: isDisputed && isParty && !myRecordFiled,
    // Records are write-once on chain: nobody can edit a filed statement,
    // their own included.
    canEditStatement: false,
    // Only the side that has stayed silent may waive, and only once.
    canWaiveResponse: isDisputed && isParty && !myRecordFiled && !waived,
    canResolve: isDisputed && canResolve,
    resolveBlockedReason,
  };
}
