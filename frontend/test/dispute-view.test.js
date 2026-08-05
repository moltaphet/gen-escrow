/* ============================================================
   Unit tests for the dispute panel's per-role logic.

   Run with Node's built-in runner (no dependencies, no install):

       cd frontend && npm test

   These cover the reviewer's "both UI roles" requirement: the buyer and the
   seller each see BOTH evidence records, but each may only submit their OWN
   statement, and the response-window badges/gating render correctly for both.
   ============================================================ */

import test from "node:test";
import assert from "node:assert/strict";

import {
  partyRole, formatCountdown, disputeBadge, disputeRecords, disputeCapabilities,
  canOpenDispute, ROLE_BUYER, ROLE_SELLER, ROLE_OBSERVER,
} from "../js/dispute-view.js";

const BUYER = "0x1111111111111111111111111111111111111111";
const SELLER = "0x2222222222222222222222222222222222222222";
const OUTSIDER = "0x3333333333333333333333333333333333333333";

const WINDOW = 48 * 60 * 60;

/** Escrow payload shaped exactly like the contract's get_escrow() result. */
function escrow(overrides = {}) {
  return {
    id: 1,
    buyer: BUYER,
    seller: SELLER,
    status: "DISPUTED",
    buyer_dispute_reason: "",
    buyer_dispute_evidence: "",
    buyer_dispute_at: "",
    seller_dispute_reason: "",
    seller_dispute_evidence: "",
    seller_dispute_at: "",
    dispute_response_phase: "NONE",
    dispute_awaiting_party: "",
    dispute_response_seconds_remaining: 0,
    dispute_response_window_expired: false,
    dispute_response_waived: false,
    can_resolve: false,
    ...overrides,
  };
}

/** Buyer has filed; the seller still holds the floor. */
function awaitingSeller(overrides = {}) {
  return escrow({
    buyer_dispute_reason: "Nothing matching the terms was delivered",
    buyer_dispute_evidence: "https://buyer.example/proof",
    buyer_dispute_at: "2026-07-01T00:00:00+00:00",
    dispute_response_phase: "AWAITING_RESPONSE",
    dispute_awaiting_party: ROLE_SELLER,
    dispute_response_seconds_remaining: WINDOW,
    can_resolve: false,
    ...overrides,
  });
}

/** Seller has filed; the buyer still holds the floor. */
function awaitingBuyer(overrides = {}) {
  return escrow({
    seller_dispute_reason: "Delivery was made exactly as agreed",
    seller_dispute_evidence: "https://seller.example/proof",
    seller_dispute_at: "2026-07-01T00:00:00+00:00",
    dispute_response_phase: "AWAITING_RESPONSE",
    dispute_awaiting_party: ROLE_BUYER,
    dispute_response_seconds_remaining: WINDOW,
    can_resolve: false,
    ...overrides,
  });
}

function bothFiled(overrides = {}) {
  return escrow({
    buyer_dispute_reason: "Nothing matching the terms was delivered",
    buyer_dispute_evidence: "https://buyer.example/proof",
    buyer_dispute_at: "2026-07-01T00:00:00+00:00",
    seller_dispute_reason: "Delivery was made exactly as agreed",
    seller_dispute_evidence: "https://seller.example/proof",
    seller_dispute_at: "2026-07-01T06:00:00+00:00",
    dispute_response_phase: "BOTH_SUBMITTED",
    dispute_awaiting_party: "",
    dispute_response_seconds_remaining: 0,
    can_resolve: true,
    ...overrides,
  });
}

/* ---------------------------------------------------------------- role */

test("partyRole identifies each side, case-insensitively", () => {
  const esc = escrow();
  assert.equal(partyRole(esc, BUYER), ROLE_BUYER);
  assert.equal(partyRole(esc, SELLER), ROLE_SELLER);
  assert.equal(partyRole(esc, BUYER.toUpperCase()), ROLE_BUYER);
  assert.equal(partyRole(esc, OUTSIDER), ROLE_OBSERVER);
  assert.equal(partyRole(esc, null), ROLE_OBSERVER);
});

/* ----------------------------------------------------------- countdown */

test("formatCountdown renders a compact remaining time", () => {
  assert.equal(formatCountdown(WINDOW), "2d 0h");
  assert.equal(formatCountdown(90000), "1d 1h");
  assert.equal(formatCountdown(3660), "1h 1m");
  assert.equal(formatCountdown(600), "10m");
  assert.equal(formatCountdown(45), "45s");
  assert.equal(formatCountdown(0), "0m");
  // Never renders a negative clock, whatever the chain returns.
  assert.equal(formatCountdown(-100), "0m");
  assert.equal(formatCountdown(undefined), "0m");
});

/* --------------------------------------------------------------- badge */

test("no badge outside an active dispute", () => {
  assert.equal(disputeBadge(escrow({ status: "FUNDED" })), null);
  assert.equal(disputeBadge(escrow({ status: "RESOLVED" })), null);
});

test("badge reads 'Awaiting Counter-Evidence' with the countdown", () => {
  const badge = disputeBadge(awaitingSeller());
  assert.equal(badge.tone, "await");
  assert.match(badge.label, /^Awaiting Counter-Evidence/);
  assert.match(badge.label, /2d 0h left/);
  assert.match(badge.detail, /seller/);
});

test("badge reads 'Response Window Expired' once the window lapses", () => {
  const badge = disputeBadge(awaitingSeller({
    dispute_response_phase: "WINDOW_EXPIRED",
    dispute_response_window_expired: true,
    dispute_response_seconds_remaining: 0,
    can_resolve: true,
  }));
  assert.equal(badge.tone, "expired");
  assert.equal(badge.label, "Response Window Expired");
});

test("badge reflects an explicit waiver", () => {
  const badge = disputeBadge(awaitingSeller({
    dispute_response_phase: "RESPONSE_WAIVED",
    dispute_response_waived: true,
    can_resolve: true,
  }));
  assert.equal(badge.tone, "waived");
  assert.equal(badge.label, "Response Waived");
});

test("badge reflects a complete two-sided record", () => {
  const badge = disputeBadge(bothFiled());
  assert.equal(badge.tone, "ready");
  assert.equal(badge.label, "Both Statements Filed");
});

/* ------------------------------------------------------------- records */

test("both records are always returned, for both roles", () => {
  for (const viewer of [BUYER, SELLER, OUTSIDER]) {
    const recs = disputeRecords(bothFiled(), viewer);
    assert.equal(recs.length, 2);
    assert.deepEqual(recs.map((r) => r.party), [ROLE_BUYER, ROLE_SELLER]);
    assert.equal(recs[0].statement, "Nothing matching the terms was delivered");
    assert.equal(recs[0].evidence, "https://buyer.example/proof");
    assert.equal(recs[1].statement, "Delivery was made exactly as agreed");
    assert.equal(recs[1].evidence, "https://seller.example/proof");
    assert.ok(recs.every((r) => r.filed));
  }
});

test("each role sees exactly one record marked as its own", () => {
  const asBuyer = disputeRecords(bothFiled(), BUYER);
  assert.deepEqual(asBuyer.map((r) => r.isYou), [true, false]);

  const asSeller = disputeRecords(bothFiled(), SELLER);
  assert.deepEqual(asSeller.map((r) => r.isYou), [false, true]);

  const asOutsider = disputeRecords(bothFiled(), OUTSIDER);
  assert.deepEqual(asOutsider.map((r) => r.isYou), [false, false]);
});

test("an unfiled record is surfaced as awaiting, not hidden", () => {
  const [buyerRec, sellerRec] = disputeRecords(awaitingSeller(), BUYER);
  assert.equal(buyerRec.filed, true);
  assert.equal(sellerRec.filed, false);
  assert.equal(sellerRec.statement, "");
  assert.equal(sellerRec.awaiting, true);
  assert.equal(buyerRec.awaiting, false);
});

/* -------------------------------------------------- capabilities: buyer */

test("buyer who has filed gets no submit and no edit action", () => {
  const caps = disputeCapabilities(awaitingSeller(), BUYER);
  assert.equal(caps.role, ROLE_BUYER);
  assert.equal(caps.myRecordFiled, true);
  assert.equal(caps.canFileStatement, false);
  // Records are write-once on chain, so editing is never offered.
  assert.equal(caps.canEditStatement, false);
  // The initiator must not be able to waive the other side's reply.
  assert.equal(caps.canWaiveResponse, false);
  assert.equal(caps.canResolve, false);
  assert.match(caps.resolveBlockedReason, /seller responds/);
});

test("buyer who has NOT filed may submit their own statement or waive", () => {
  const caps = disputeCapabilities(awaitingBuyer(), BUYER);
  assert.equal(caps.role, ROLE_BUYER);
  assert.equal(caps.myRecordFiled, false);
  assert.equal(caps.canFileStatement, true);
  assert.equal(caps.canWaiveResponse, true);
});

/* ------------------------------------------------- capabilities: seller */

test("seller who has NOT filed may submit their own statement or waive", () => {
  const caps = disputeCapabilities(awaitingSeller(), SELLER);
  assert.equal(caps.role, ROLE_SELLER);
  assert.equal(caps.myRecordFiled, false);
  assert.equal(caps.canFileStatement, true);
  assert.equal(caps.canWaiveResponse, true);
  assert.equal(caps.canResolve, false);
});

test("seller who has filed gets no submit and no edit action", () => {
  const caps = disputeCapabilities(awaitingBuyer(), SELLER);
  assert.equal(caps.myRecordFiled, true);
  assert.equal(caps.canFileStatement, false);
  assert.equal(caps.canEditStatement, false);
  assert.equal(caps.canWaiveResponse, false);
});

/* ----------------------------------------------- capabilities: symmetry */

test("neither party can submit once both records exist", () => {
  for (const viewer of [BUYER, SELLER]) {
    const caps = disputeCapabilities(bothFiled(), viewer);
    assert.equal(caps.canFileStatement, false);
    assert.equal(caps.canEditStatement, false);
    assert.equal(caps.canWaiveResponse, false);
    // Two-sided record -> resolution is unlocked for both roles.
    assert.equal(caps.canResolve, true);
    assert.equal(caps.resolveBlockedReason, "");
  }
});

test("an outsider gets no write actions at all", () => {
  const caps = disputeCapabilities(awaitingSeller(), OUTSIDER);
  assert.equal(caps.role, ROLE_OBSERVER);
  assert.equal(caps.isParty, false);
  assert.equal(caps.canFileStatement, false);
  assert.equal(caps.canWaiveResponse, false);
});

test("a waived response unlocks resolve and retires the waive action", () => {
  const esc = awaitingSeller({
    dispute_response_phase: "RESPONSE_WAIVED",
    dispute_response_waived: true,
    can_resolve: true,
  });
  const sellerCaps = disputeCapabilities(esc, SELLER);
  assert.equal(sellerCaps.canWaiveResponse, false, "cannot waive twice");
  assert.equal(sellerCaps.canResolve, true);
  // The seller may still change their mind and file before resolution lands,
  // matching the contract's behaviour.
  assert.equal(sellerCaps.canFileStatement, true);

  assert.equal(disputeCapabilities(esc, BUYER).canResolve, true);
});

test("an expired window unlocks resolve for both roles", () => {
  const esc = awaitingSeller({
    dispute_response_phase: "WINDOW_EXPIRED",
    dispute_response_window_expired: true,
    dispute_response_seconds_remaining: 0,
    can_resolve: true,
  });
  assert.equal(disputeCapabilities(esc, BUYER).canResolve, true);
  assert.equal(disputeCapabilities(esc, SELLER).canResolve, true);
  assert.equal(disputeCapabilities(esc, OUTSIDER).canResolve, true);
});

test("no dispute actions outside the DISPUTED state", () => {
  const funded = escrow({ status: "FUNDED" });
  for (const viewer of [BUYER, SELLER]) {
    const caps = disputeCapabilities(funded, viewer);
    assert.equal(caps.canFileStatement, false);
    assert.equal(caps.canWaiveResponse, false);
    assert.equal(caps.canResolve, false);
  }
});

/* -------------------------------------------------- opening a dispute */

test("only the two counterparties may open a dispute", () => {
  for (const status of ["FUNDED", "DELIVERY_SUBMITTED"]) {
    const esc = escrow({ status });
    assert.equal(canOpenDispute(esc, BUYER), true);
    assert.equal(canOpenDispute(esc, SELLER), true);
    // An observer must not be offered an action the contract rejects.
    assert.equal(canOpenDispute(esc, OUTSIDER), false);
    assert.equal(canOpenDispute(esc, null), false);
  }
});

test("a dispute cannot be opened from a non-disputable state", () => {
  for (const status of ["DISPUTED", "RESOLVED", "COMPLETED", "REFUNDED", "EXPIRED"]) {
    const esc = escrow({ status });
    assert.equal(canOpenDispute(esc, BUYER), false);
    assert.equal(canOpenDispute(esc, SELLER), false);
  }
});
