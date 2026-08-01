/* ============================================================
   contract.js — GenLayer contract interaction layer
   ============================================================ */

import { STATE, NETWORK } from "./wallet.js";

export const RPC_URL = "https://studio.genlayer.com/api";

// Deployed GenEscrow contract on GenLayer StudioNet.
export const CONTRACT_ADDRESS = "0x4588A9A9F87500961260885F9C9D23CFC9e9fa2B";

let GL = null;
let CHAINS = null;
export let sdkReady = false;
export let sdkError = null;

try {
  GL = await import("https://esm.sh/genlayer-js@1.1.8");
  CHAINS = await import("https://esm.sh/genlayer-js@1.1.8/chains");
  sdkReady = true;
} catch (e) {
  try {
    GL = await import("https://esm.sh/genlayer-js");
    CHAINS = await import("https://esm.sh/genlayer-js/chains");
    sdkReady = true;
  } catch (e2) {
    sdkError = e2;
    console.error("[gen-escrow] genlayer-js load failed:", e2);
  }
}

function chain() {
  if (CHAINS && CHAINS.studionet) return CHAINS.studionet;
  return {
    id: parseInt(NETWORK.chainIdHex, 16),
    name: NETWORK.chainName,
    rpcUrls: { default: { http: [RPC_URL] } },
    nativeCurrency: NETWORK.nativeCurrency,
  };
}

let readClient = null;
function getReadClient() {
  if (!sdkReady) throw new Error("genlayer-js not loaded");
  if (!readClient) readClient = GL.createClient({ chain: chain() });
  return readClient;
}

function getWriteClient() {
  if (!sdkReady) throw new Error("genlayer-js not loaded");
  if (!STATE.account) throw new Error("Wallet not connected");
  // Pass the injected wallet provider so genlayer-js signs writes through
  // MetaMask, and the connected address as the account.
  return GL.createClient({
    chain: chain(),
    account: STATE.account,
    provider: (typeof window !== "undefined" && window.ethereum) || undefined,
  });
}

// Submit a write and wait until it is ACCEPTED before returning, so the
// contract-state reads this UI performs (escrow status, claimable) reflect the
// change on the next refresh. ACCEPTED is sufficient here because the UI reads
// contract storage, not native EOA balances (those settle at FINALIZED). The
// SDK's default wait budget is short (~30s), so we widen it to tolerate slower
// StudioNet rounds instead of silently giving up.
async function submitWrite(client, functionName, kwargs, value = 0n) {
  const txHash = await client.writeContract({
    address: CONTRACT_ADDRESS,
    functionName,
    kwargs,
    value,
  });
  try {
    return await client.waitForTransactionReceipt({
      hash: txHash,
      status: "ACCEPTED",
      interval: 3000,
      retries: 40,
    });
  } catch (e) {
    // The transaction was already submitted; only the wait failed. Surface it
    // so callers do not report a premature success, but keep the hash for logs.
    console.warn("[gen-escrow] waitForTransactionReceipt failed", e);
    throw new Error("Transaction submitted but confirmation timed out: " + (e.message || e));
  }
}

/* ---------- Address helpers ---------- */
export function getContractAddress() { return CONTRACT_ADDRESS; }
export function hasContract() { return /^0x[0-9a-fA-F]{40}$/.test(CONTRACT_ADDRESS) && CONTRACT_ADDRESS !== "0x0000000000000000000000000000000000000000"; }

function asObj(v) {
  if (v instanceof Map) return Object.fromEntries(v);
  return v || {};
}
function num(v) {
  if (typeof v === "bigint") return Number(v);
  if (v == null) return 0;
  return Number(v);
}
function big(v) {
  if (typeof v === "bigint") return v;
  try { return BigInt(v); } catch { return 0n; }
}

/* ============================================================
   READ METHODS
   ============================================================ */

export async function getStats() {
  if (!hasContract()) throw new Error("Contract address not set");
  const client = getReadClient();
  const res = await client.readContract({ address: CONTRACT_ADDRESS, functionName: "get_stats", args: [] });
  return asObj(res);
}

export async function getEscrow(id) {
  const client = getReadClient();
  const res = await client.readContract({
    address: CONTRACT_ADDRESS,
    functionName: "get_escrow",
    kwargs: { escrow_id: Number(id) },
  });
  return asObj(res);
}

export async function getMyEscrows(account, role = "all") {
  const client = getReadClient();

  // Fetch role id-lists concurrently. Use allSettled so a single failed read
  // (e.g. seller index) never wipes the other role's results.
  const roleReads = [];
  if (role === "buyer" || role === "all") {
    roleReads.push(client.readContract({ address: CONTRACT_ADDRESS, functionName: "get_escrows_by_buyer", kwargs: { buyer: account } }));
  }
  if (role === "seller" || role === "all") {
    roleReads.push(client.readContract({ address: CONTRACT_ADDRESS, functionName: "get_escrows_by_seller", kwargs: { seller: account } }));
  }

  let ids = [];
  const roleResults = await Promise.allSettled(roleReads);
  for (const r of roleResults) {
    if (r.status === "fulfilled" && r.value) ids = ids.concat(r.value);
    else if (r.status === "rejected") console.warn("Failed to load escrow index", r.reason);
  }

  // Dedup + fetch details concurrently; one bad escrow must not drop the rest.
  const unique = [...new Set(ids.map(Number))];
  const settled = await Promise.allSettled(unique.map((id) => getEscrow(id)));
  const escrows = [];
  settled.forEach((r, i) => {
    if (r.status === "fulfilled") escrows.push(r.value);
    else console.warn("Failed to load escrow", unique[i], r.reason);
  });
  return escrows.sort((a, b) => Number(b.id) - Number(a.id));
}

export async function getClaimable(account) {
  const client = getReadClient();
  const val = await client.readContract({
    address: CONTRACT_ADDRESS,
    functionName: "get_claimable",
    kwargs: { addr: account },
  });
  return big(val);
}

/* ============================================================
   WRITE METHODS (require connected wallet + correct network)
   ============================================================ */

export async function createEscrow({ seller, amountGen, title, description, terms, deadline }) {
  if (!hasContract()) throw new Error("Contract address not configured");
  const client = getWriteClient();
  const amountAtto = genToAtto(amountGen);

  return submitWrite(
    client,
    "create_escrow",
    {
      seller,
      title,
      description: description || "",
      terms,
      deadline_iso: deadline || "",
    },
    amountAtto,
  );
}

export async function submitDelivery(escrowId, note, evidence) {
  const client = getWriteClient();
  return submitWrite(client, "submit_delivery", {
    escrow_id: Number(escrowId),
    note,
    evidence: evidence || "",
  });
}

export async function release(escrowId) {
  const client = getWriteClient();
  return submitWrite(client, "release", { escrow_id: Number(escrowId) });
}

export async function refund(escrowId) {
  const client = getWriteClient();
  return submitWrite(client, "refund", { escrow_id: Number(escrowId) });
}

export async function raiseDispute(escrowId, reason, evidence) {
  const client = getWriteClient();
  return submitWrite(client, "raise_dispute", {
    escrow_id: Number(escrowId),
    reason,
    evidence: evidence || "",
  });
}

export async function resolveDispute(escrowId) {
  const client = getWriteClient();
  return submitWrite(client, "resolve_dispute", { escrow_id: Number(escrowId) });
}

export async function claimAfterDeadline(escrowId) {
  const client = getWriteClient();
  return submitWrite(client, "claim_after_deadline", { escrow_id: Number(escrowId) });
}

export async function claim() {
  const client = getWriteClient();
  return submitWrite(client, "claim", {});
}

/* ============================================================
   Formatting helpers
   ============================================================ */

export function shortAddr(a) {
  if (!a) return "—";
  const s = String(a);
  return s.slice(0, 6) + "…" + s.slice(-4);
}

export function formatGEN(atto, maxDecimals = 4) {
  let v = 0n;
  try { v = typeof atto === "bigint" ? atto : BigInt(atto); } catch { v = 0n; }
  const base = 10n ** 18n;
  const whole = v / base;
  const frac = v % base;
  if (frac === 0n) return whole.toString();
  let fracStr = frac.toString().padStart(18, "0").slice(0, maxDecimals).replace(/0+$/, "");
  return fracStr ? `${whole}.${fracStr}` : whole.toString();
}

export function genToAtto(gen) {
  const s = String(gen).trim();
  if (!s || isNaN(Number(s))) return 0n;
  const [w, f = ""] = s.split(".");
  const frac = (f + "0".repeat(18)).slice(0, 18);
  return BigInt(w || "0") * 10n ** 18n + BigInt(frac || "0");
}

export function statusClass(status) {
  return `status status--${status || "FUNDED"}`;
}
