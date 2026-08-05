/* ============================================================
   app.js — Main application orchestration for gen-escrow
   ============================================================ */

import {
  STATE, onWallet, connectWallet, silentReconnect, bindNetworkUI,
  applyNetworkState, hasWallet,
} from "./wallet.js";

import * as C from "./contract.js";
import * as UI from "./ui.js";

const $ = (id) => document.getElementById(id);

let currentFilter = "all";
let myEscrowsCache = [];

/* ============================================================
   Write guard
   ============================================================ */
function canWrite() {
  return C.sdkReady && C.hasContract() && STATE.connected && STATE.correctNetwork;
}

function setWriteButtonStates() {
  const enabled = canWrite();
  document.querySelectorAll("[data-write]").forEach((el) => {
    el.disabled = !enabled;
  });
}

/* ============================================================
   Load stats (global counters)
   ============================================================ */
async function loadStats() {
  try {
    const stats = await C.getStats();
    const totalEl = $("stat-total");
    const volEl = $("stat-volume");

    if (totalEl) totalEl.textContent = Number(stats.total_escrows || 0);
    if (volEl) volEl.textContent = C.formatGEN(stats.total_volume_atto || 0) + " GEN";
  } catch (e) {
    // Silent fail for stats — not critical
    console.warn("[gen-escrow] stats load failed", e);
  }
}

/* ============================================================
   Load and render my escrows
   ============================================================ */
async function loadMyEscrows() {
  const grid = $("escrow-grid");
  const empty = $("escrow-empty");
  if (!grid) return;

  grid.innerHTML = `<div style="padding:20px;color:#5c5c66">Loading...</div>`;

  if (!STATE.connected || !STATE.account) {
    grid.innerHTML = "";
    empty?.classList.remove("hidden");
    return;
  }

  try {
    const escrows = await C.getMyEscrows(STATE.account, currentFilter);
    myEscrowsCache = escrows;

    if (!escrows.length) {
      grid.innerHTML = "";
      empty?.classList.remove("hidden");
      return;
    }

    empty?.classList.add("hidden");
    grid.innerHTML = escrows.map(UI.renderEscrowCard).join("");

    // Wire click handlers
    grid.querySelectorAll(".escrow-card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = Number(card.dataset.id);
        const esc = myEscrowsCache.find((e) => Number(e.id) === id);
        if (esc) openEscrowDetail(esc);
      });
    });
  } catch (e) {
    console.error("[gen-escrow] loadMyEscrows error", e);
    grid.innerHTML = `<div style="color:#e57373;padding:16px">Failed to load escrows: ${e.message}</div>`;
  }
}

function openEscrowDetail(esc) {
  const handlers = {
    release: async (id) => {
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Releasing funds...", "ok");
        await C.release(id);
        UI.showToast("Release transaction sent. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Release failed: " + (err.message || err), "err");
      }
    },
    submitDelivery: async (id) => {
      const note = prompt("Describe what you delivered (deliverables, scope covered):");
      if (!note) return;
      const evidence = prompt("Add delivery evidence or links (optional)", "") || "";
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Submitting delivery...", "ok");
        await C.submitDelivery(id, note, evidence);
        UI.showToast("Delivery submitted. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Submit delivery failed: " + (err.message || err), "err");
      }
    },
    refund: async (id) => {
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Requesting refund...", "ok");
        await C.refund(id);
        UI.showToast("Refund sent. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Refund failed: " + (err.message || err), "err");
      }
    },
    raiseDispute: async (id) => {
      const reason = prompt("Why are you raising a dispute? (be specific)");
      if (!reason) return;
      const evidence = prompt("Add any evidence or links (optional)", "") || "";
      if (!canWrite()) return alert("Connect + StudioNet required");
      try {
        UI.showToast("Raising dispute...", "ok");
        await C.raiseDispute(id, reason, evidence);
        UI.showToast("Dispute raised. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Dispute failed: " + (err.message || err), "err");
      }
    },
    resolve: async (id) => {
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Submitting to AI consensus (this may take 30–90s)...", "ok");
        await C.resolveDispute(id);
        UI.showToast("Dispute resolved. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Resolution failed: " + (err.message || err), "err");
      }
    },
    // File the CONNECTED party's own counter-statement. Each side owns a
    // separate, write-once record on chain, so this only ever submits the
    // caller's own text — it never reads or re-sends the counterparty's record.
    submitMyStatement: async (id, role) => {
      const side = role === "BUYER" ? "buyer" : "seller";
      const reason = prompt(
        `Your ${side} statement — describe your side of the dispute (min 10 characters):`
      );
      if (!reason) return;
      const evidence = prompt("Your evidence links (optional, one per line):", "") || "";
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Filing your statement...", "ok");
        await C.raiseDispute(id, reason, evidence);
        UI.showToast("Your statement is on the record. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Submission failed: " + (err.message || err), "err");
      }
    },
    waiveResponse: async (id) => {
      if (!confirm(
        "Waive your response?\n\nYou are giving up your slot to file a counter-statement, " +
        "and the dispute becomes resolvable immediately instead of after the 48h window. " +
        "This cannot be undone."
      )) return;
      if (!canWrite()) return alert("Connect wallet and switch to StudioNet");
      try {
        UI.showToast("Waiving your response...", "ok");
        await C.waiveDisputeResponse(id);
        UI.showToast("Response waived. Refreshing...", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Waiver failed: " + (err.message || err), "err");
      }
    },
    claimAfterDeadline: async (id) => {
      if (!canWrite()) return;
      try {
        UI.showToast("Claiming after deadline...", "ok");
        await C.claimAfterDeadline(id);
        UI.showToast("Claim sent.", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Claim failed: " + err.message, "err");
      }
    },
    claim: async () => {
      if (!canWrite()) return;
      try {
        UI.showToast("Claiming your balance...", "ok");
        await C.claim();
        UI.showToast("Claim transaction submitted.", "ok");
        UI.closeModal();
        await refreshAll();
      } catch (err) {
        UI.showToast("Claim failed: " + (err.message || err), "err");
      }
    },
  };

  UI.openModal(esc, STATE.account, handlers);
}

/* ============================================================
   Create form handling
   ============================================================ */
function bindCreateForm() {
  const form = $("create-form");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!canWrite()) {
      UI.showToast("Please connect wallet and switch to GenLayer StudioNet first.", "err");
      return;
    }

    const seller = $("seller").value.trim();
    const amount = $("amount").value.trim();
    const title = $("title").value.trim();
    const description = $("description").value.trim();
    const terms = $("terms").value.trim();
    const deadline = $("deadline").value.trim();

    if (!seller || !amount || !title || !terms) {
      UI.showToast("Please fill seller, amount, title and terms.", "err");
      return;
    }

    const submitBtn = form.querySelector("button[type=submit]");
    const statusEl = $("create-status");

    if (submitBtn) submitBtn.classList.add("loading");
    if (statusEl) statusEl.textContent = "Submitting transaction...";

    try {
      await C.createEscrow({
        seller,
        amountGen: amount,
        title,
        description,
        terms,
        deadline,
      });

      UI.showToast("Escrow created successfully!", "ok");
      form.reset();

      // Refresh everything
      await refreshAll();
    } catch (err) {
      console.error(err);
      UI.showToast("Creation failed: " + (err.message || err), "err");
    } finally {
      if (submitBtn) submitBtn.classList.remove("loading");
      if (statusEl) statusEl.textContent = "";
    }
  });
}

/* ============================================================
   Filter + refresh controls
   ============================================================ */
function bindFilters() {
  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      currentFilter = btn.dataset.filter || "all";
      loadMyEscrows();
    });
  });

  $("refresh-btn")?.addEventListener("click", () => {
    refreshAll();
  });
}

/* ============================================================
   Global refresh
   ============================================================ */
async function refreshAll() {
  setWriteButtonStates();
  // Independent reads — a failure in one must not block the other.
  await Promise.allSettled([loadStats(), loadMyEscrows()]);
}

/* ============================================================
   Wallet + network listeners
   ============================================================ */
function bindWalletEvents() {
  onWallet("connect", async () => {
    setWriteButtonStates();
    await loadMyEscrows();
  });

  onWallet("disconnect", () => {
    const grid = $("escrow-grid");
    if (grid) grid.innerHTML = "";
    $("escrow-empty")?.classList.remove("hidden");
    setWriteButtonStates();
  });

  onWallet("networkchange", ({ correct }) => {
    setWriteButtonStates();
    if (correct) {
      // Auto-refresh data when user switches to correct network
      loadMyEscrows();
    }
  });
}

/* ============================================================
   Contract address copy helper (footer)
   ============================================================ */
window.copyContractAddress = function () {
  const addr = C.getContractAddress();
  if (!addr || addr.includes("0000")) {
    UI.showToast("Contract address not yet deployed. Update CONTRACT_ADDRESS in js/contract.js", "err");
    return;
  }
  navigator.clipboard?.writeText(addr).then(() => {
    UI.showToast("Contract address copied", "ok");
  });
};

/* ============================================================
   Boot
   ============================================================ */
export async function initApp() {
  // Bind wallet UI (connect, network guard, etc)
  bindNetworkUI();

  // Initial reconnect attempt
  await silentReconnect();

  // Bind UI handlers
  bindCreateForm();
  bindFilters();
  bindWalletEvents();

  // Initial data load
  await loadStats();

  // If already connected, load escrows
  if (STATE.connected && STATE.account) {
    await loadMyEscrows();
  }

  // Periodic light refresh of stats
  setInterval(() => {
    if (!document.hidden) loadStats();
  }, 45000);

  // Make sure buttons reflect current state
  setWriteButtonStates();

  // Keyboard escape for modal
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const modal = $("modal");
      if (modal && !modal.classList.contains("hidden")) modal.classList.add("hidden");
    }
  });

  // Click outside modal content closes it
  $("modal")?.addEventListener("click", (e) => {
    if (e.target.id === "modal" || e.target.classList.contains("modal__backdrop")) {
      $("modal").classList.add("hidden");
    }
  });

  // Helpful console banner
  console.log("%c[gen-escrow] UI ready. Update CONTRACT_ADDRESS in js/contract.js after deploying.", "color:#5c5c66");
}
