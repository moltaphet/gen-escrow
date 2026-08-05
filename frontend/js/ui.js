/* ============================================================
   ui.js — rendering, modal, toasts, helpers
   ============================================================ */

import * as C from "./contract.js";
import {
  disputeBadge, disputeRecords, disputeCapabilities, formatCountdown,
  ROLE_BUYER, ROLE_SELLER,
} from "./dispute-view.js";

const $ = (id) => document.getElementById(id);

export function shortAddr(a) { return C.shortAddr(a); }
export function formatGEN(atto) { return C.formatGEN(atto); }

/* ---------- Toasts ---------- */
let toastId = 0;
export function showToast(message, type = "ok") {
  const container = $("toast-container");
  if (!container) return alert(message);

  const el = document.createElement("div");
  el.className = `toast toast--${type}`;
  el.textContent = message;
  container.appendChild(el);

  setTimeout(() => {
    el.style.transition = "opacity .25s";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 200);
  }, 4200);
}

/* ---------- Status badge ---------- */
export function renderStatus(status) {
  const s = status || "FUNDED";
  return `<span class="${C.statusClass(s)}">${s}</span>`;
}

/* ---------- Escrow card ---------- */
export function renderEscrowCard(esc) {
  const id = Number(esc.id);
  const amount = C.formatGEN(esc.net_amount_atto ?? esc.amount_atto);

  return `
    <div class="escrow-card" data-id="${id}">
      <div class="escrow-card__header">
        <div>
          <div class="escrow-card__id">#${id}</div>
          <div class="escrow-card__amount">${amount} GEN</div>
        </div>
        ${renderStatus(esc.status)}
      </div>

      <div class="escrow-card__title">${escapeHtml(esc.title || "Untitled")}</div>

      <div class="escrow-card__parties">
        <span><strong>Buyer:</strong> ${shortAddr(esc.buyer)}</span>
        <span><strong>Seller:</strong> ${shortAddr(esc.seller)}</span>
      </div>

      ${renderCardDisputeBadge(esc)}
    </div>
  `;
}

/** Compact response-window badge on the card, so the grid shows at a glance
 *  which disputes are still waiting on counter-evidence. */
function renderCardDisputeBadge(esc) {
  const badge = disputeBadge(esc);
  if (!badge) return "";
  return `<div class="card-respwin card-respwin--${badge.tone}">${escapeHtml(badge.label)}</div>`;
}

/* ---------- Full modal content ---------- */
export function renderEscrowModal(esc, currentAccount) {
  const isBuyer = String(currentAccount).toLowerCase() === String(esc.buyer).toLowerCase();
  const isSeller = String(currentAccount).toLowerCase() === String(esc.seller).toLowerCase();
  const canAct = isBuyer || isSeller;

  let html = `
    <h3 class="modal__title">#${esc.id} — ${escapeHtml(esc.title)}</h3>

    <div class="modal__meta">
      ${renderStatus(esc.status)}
      <span style="color:#5c5c66">•</span>
      <span style="font-family:ui-monospace,monospace;font-size:13px">${C.formatGEN(esc.net_amount_atto ?? esc.amount_atto)} GEN</span>
    </div>

    <div class="modal__row">
      <div class="modal__label">Buyer</div>
      <div class="modal__value mono">${esc.buyer}</div>
    </div>
    <div class="modal__row">
      <div class="modal__label">Seller</div>
      <div class="modal__value mono">${esc.seller}</div>
    </div>

    <div class="modal__row">
      <div class="modal__label">Description</div>
      <div class="modal__value">${escapeHtml(esc.description) || "<em>No description</em>"}</div>
    </div>

    <div class="modal__row">
      <div class="modal__label">Release Terms / Conditions</div>
      <div class="modal__value" style="white-space:pre-wrap">${escapeHtml(esc.terms) || "—"}</div>
    </div>

    <div class="modal__row">
      <div class="modal__label">Deadline</div>
      <div class="modal__value">${escapeHtml(esc.deadline_iso) || "—"}</div>
    </div>
  `;

  if (esc.delivery_note) {
    html += `
      <div class="modal__row">
        <div class="modal__label">Delivery</div>
        <div class="modal__value"><strong>Note:</strong> ${escapeHtml(esc.delivery_note)}</div>
        ${esc.delivery_evidence ? `<div style="margin-top:6px"><strong>Evidence:</strong><br>${escapeHtml(esc.delivery_evidence)}</div>` : ""}
      </div>
    `;
  }

  if (esc.status === "DISPUTED" || esc.status === "RESOLVED") {
    html += renderDisputePanel(esc, currentAccount);
  }

  if (esc.status === "RESOLVED" || esc.status === "COMPLETED" || esc.status === "EXPIRED") {
    html += `
      <div class="modal__row">
        <div class="modal__label">Resolution</div>
        <div class="modal__value">
          ${esc.resolved_winner ? `<strong>${esc.resolved_winner}</strong> - ${bpsToPct(esc.resolved_release_bps)}%<br>` : ""}
          ${escapeHtml(esc.resolution_reason) || ""}
        </div>
      </div>
    `;
  }

  return html;
}

/* ---------- Dispute panel: both records + response window ---------- */

/** One party's attributable statement + evidence. */
function renderDisputeRecord(rec) {
  const mine = rec.isYou
    ? `<span class="record__you">You</span>`
    : "";
  const awaiting = rec.awaiting
    ? `<span class="record__awaiting">Awaiting response</span>`
    : "";

  // An unfiled record is shown explicitly rather than hidden, so each side can
  // see at a glance that the other has not gone on the record yet.
  const body = rec.filed
    ? `
      <div class="record__statement">${escapeHtml(rec.statement) || "—"}</div>
      ${rec.evidence
        ? `<div class="record__evidence"><strong>Evidence:</strong><br>${escapeHtml(rec.evidence)}</div>`
        : `<div class="record__empty">No evidence links provided.</div>`}
      ${rec.filedAt ? `<div class="record__meta">Filed ${escapeHtml(rec.filedAt)}</div>` : ""}
    `
    : `<div class="record__empty">No statement filed by this party yet.</div>`;

  return `
    <div class="record ${rec.filed ? "record--filed" : "record--empty"}${rec.isYou ? " record--mine" : ""}">
      <div class="record__head">
        <span class="record__label">${escapeHtml(rec.label)}</span>
        ${mine}${awaiting}
      </div>
      ${body}
    </div>
  `;
}

export function renderDisputePanel(esc, currentAccount) {
  const badge = disputeBadge(esc);
  const records = disputeRecords(esc, currentAccount);
  const caps = disputeCapabilities(esc, currentAccount);

  const badgeHtml = badge
    ? `
      <div class="respwin respwin--${badge.tone}">
        <div class="respwin__label">${escapeHtml(badge.label)}</div>
        ${badge.detail ? `<div class="respwin__detail">${escapeHtml(badge.detail)}</div>` : ""}
      </div>
    `
    : "";

  // Tell a connected party where they stand, without offering an action the
  // contract would reject.
  let ownNotice = "";
  if (esc.status === "DISPUTED" && caps.isParty) {
    const side = caps.role === ROLE_BUYER ? "buyer" : "seller";
    ownNotice = caps.myRecordFiled
      ? `<div class="respwin__note">Your ${side} statement is on the record. It is write-once and cannot be edited or overwritten.</div>`
      : `<div class="respwin__note">You have not filed your ${side} statement yet. You can only submit your own record.</div>`;
  }

  return `
    <div class="modal__row">
      <div class="modal__label">Dispute — both parties' records</div>
      ${badgeHtml}
      <div class="records">
        ${records.map(renderDisputeRecord).join("")}
      </div>
      ${ownNotice}
    </div>
  `;
}

/* ---------- Action buttons in modal ---------- */
export function renderModalActions(esc, currentAccount, handlers) {
  const container = $("modal-actions");
  if (!container) return;
  container.innerHTML = "";

  const isBuyer = String(currentAccount).toLowerCase() === String(esc.buyer).toLowerCase();
  const isSeller = String(currentAccount).toLowerCase() === String(esc.seller).toLowerCase();

  const addBtn = (label, cls, handler, disabled = false) => {
    const b = document.createElement("button");
    b.className = `btn ${cls}`;
    b.textContent = label;
    if (disabled) b.disabled = true;
    b.onclick = handler;
    container.appendChild(b);
  };

  // Seller records deliverables while the escrow is still FUNDED.
  if (esc.status === "FUNDED" && isSeller) {
    addBtn("Submit Delivery", "btn--primary", () => handlers.submitDelivery(esc.id));
  }

  // Buyer may release from FUNDED (early) or after reviewing a delivery.
  if (esc.status === "FUNDED" || esc.status === "DELIVERY_SUBMITTED") {
    if (isBuyer) {
      addBtn("Release to Seller", "btn--success", () => handlers.release(esc.id));
    }
    // Refund is only available before any delivery has been submitted.
    if (isBuyer && esc.status === "FUNDED") {
      addBtn("Refund Myself", "btn--ghost", () => handlers.refund(esc.id));
    }
    addBtn("Raise Dispute", "btn--warn", () => handlers.raiseDispute(esc.id));
  }

  if (esc.status === "DISPUTED") {
    const caps = disputeCapabilities(esc, currentAccount);

    // Submit is offered only to the connected party, and only for their own,
    // not-yet-filed record. A party whose statement is already on chain gets no
    // edit action at all — records are write-once.
    if (caps.canFileStatement) {
      const side = caps.role === ROLE_BUYER ? "Buyer" : "Seller";
      addBtn(`Submit My ${side} Statement`, "btn--warn", () =>
        handlers.submitMyStatement(esc.id, caps.role));
    }

    // The silent party can forfeit the wait instead of stalling the dispute.
    if (caps.canWaiveResponse) {
      addBtn("Waive My Response", "btn--ghost", () => handlers.waiveResponse(esc.id));
    }

    // Resolution stays visibly disabled until the window unlocks, with the
    // reason surfaced rather than letting the user trigger a revert.
    if (caps.canResolve) {
      addBtn("Resolve with AI", "btn--primary", () => handlers.resolve(esc.id));
    } else {
      addBtn("Resolve with AI", "btn--primary", () => {}, true);
      const why = document.createElement("div");
      why.className = "actions__note";
      why.textContent = caps.resolveBlockedReason;
      container.appendChild(why);
    }
  }

  if ((esc.status === "FUNDED" || esc.status === "DELIVERY_SUBMITTED") && isSeller) {
    addBtn("Claim After Deadline", "btn--ghost", () => handlers.claimAfterDeadline(esc.id));
  }

  // Always show claim if there is balance
  const claimBtn = document.createElement("button");
  claimBtn.className = "btn";
  claimBtn.textContent = "Claim My Balance";
  claimBtn.onclick = handlers.claim;
  container.appendChild(claimBtn);

  // Close
  const close = document.createElement("button");
  close.className = "btn btn--ghost";
  close.textContent = "Close";
  close.onclick = () => $("modal").classList.add("hidden");
  container.appendChild(close);
}

/* ---------- Modal control ---------- */
export function openModal(esc, currentAccount, handlers) {
  const modal = $("modal");
  const content = $("modal-content");
  if (!modal || !content) return;

  content.innerHTML = renderEscrowModal(esc, currentAccount);
  renderModalActions(esc, currentAccount, handlers);
  modal.classList.remove("hidden");
}

export function closeModal() {
  const modal = $("modal");
  if (modal) modal.classList.add("hidden");
}

/* ---------- Utility ---------- */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function bpsToPct(bps) {
  const v = Number(bps || 0);
  return (v / 100).toFixed(v % 100 === 0 ? 0 : 1);
}
