/* ============================================================
   wallet.js — MetaMask + GenLayer StudioNet network guard
   ============================================================ */

// ---- Target network (GenLayer StudioNet) ----
export const NETWORK = {
  chainIdHex: "0xF22F",              // 61999 decimal
  chainName: "GenLayer StudioNet",
  rpcUrls: ["https://studio.genlayer.com/api"],
  blockExplorerUrls: ["https://studio.genlayer.com"],
  nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 },
};

const RECONNECT_KEY = "genescrow_reconnect";

// In-memory state
export const STATE = {
  connected: false,
  account: null,
  chainId: null,
  correctNetwork: false,
  listenersAttached: false,
  switching: false,
};

// Event bus
const bus = new EventTarget();
export function onWallet(type, handler) { bus.addEventListener(type, handler); }
function emit(type, detail) { bus.dispatchEvent(new CustomEvent(type, { detail })); }

function eth() { return window.ethereum || null; }
export function hasWallet() { return !!eth(); }

const els = {
  overlay: () => document.getElementById("network-overlay"),
  pill: () => document.getElementById("network-pill"),
  pillText: () => document.getElementById("network-pill-text"),
  switchBtn: () => document.getElementById("switch-network-btn"),
  connectBtn: () => document.getElementById("connect-btn"),
  walletAddr: () => document.getElementById("wallet-address"),
  walletAddrText: () => document.getElementById("wallet-address-text"),
  disconnectBtn: () => document.getElementById("disconnect-btn"),
};

function chainName(idHex) {
  if (!idHex) return "unknown";
  const known = {
    "0x1": "Ethereum",
    "0xaa36a7": "Sepolia",
    "0x89": "Polygon",
    [NETWORK.chainIdHex.toLowerCase()]: NETWORK.chainName,
  };
  return known[String(idHex).toLowerCase()] || `Chain ${parseInt(idHex, 16)}`;
}

export async function applyNetworkState() {
  const provider = eth();
  const pill = els.pill();
  const pillText = els.pillText();

  if (!provider || !STATE.connected) {
    STATE.correctNetwork = false;
    if (pill) pill.className = "pill pill--muted";
    if (pillText) pillText.textContent = STATE.connected ? "…" : "Not connected";
    hideOverlay();
    emit("networkchange", { correct: false });
    return false;
  }

  let chainId = null;
  try {
    chainId = await provider.request({ method: "eth_chainId" });
  } catch (e) {
    console.warn("[gen-escrow] eth_chainId failed", e);
  }

  STATE.chainId = chainId;
  const isCorrect = chainId && chainId.toLowerCase() === NETWORK.chainIdHex.toLowerCase();
  STATE.correctNetwork = isCorrect;

  if (pill) {
    pill.className = isCorrect ? "pill pill--ok" : "pill pill--muted";
  }
  if (pillText) {
    pillText.textContent = isCorrect ? NETWORK.chainName : chainName(chainId);
  }

  if (!isCorrect) {
    showOverlay(chainId);
  } else {
    hideOverlay();
  }

  emit("networkchange", { correct: isCorrect, chainId });
  return isCorrect;
}

function showOverlay(chainId) {
  const overlay = els.overlay();
  const current = document.getElementById("net-current");
  if (overlay) overlay.classList.remove("hidden");
  if (current) current.textContent = `Connected to ${chainName(chainId)}`;
}

function hideOverlay() {
  const overlay = els.overlay();
  if (overlay) overlay.classList.add("hidden");
}

export async function switchToStudioNet() {
  const provider = eth();
  if (!provider) return;

  STATE.switching = true;
  try {
    await provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: NETWORK.chainIdHex }],
    });
  } catch (switchError) {
    if (switchError.code === 4902) {
      try {
        await provider.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: NETWORK.chainIdHex,
            chainName: NETWORK.chainName,
            rpcUrls: NETWORK.rpcUrls,
            blockExplorerUrls: NETWORK.blockExplorerUrls,
            nativeCurrency: NETWORK.nativeCurrency,
          }],
        });
      } catch (addError) {
        console.error("[gen-escrow] Failed to add chain", addError);
      }
    } else {
      console.error("[gen-escrow] Failed to switch chain", switchError);
    }
  } finally {
    STATE.switching = false;
    await applyNetworkState();
  }
}

export async function connectWallet() {
  const provider = eth();
  if (!provider) {
    alert("Please install MetaMask or another Web3 wallet.");
    return;
  }

  try {
    const accounts = await provider.request({ method: "eth_requestAccounts" });
    if (!accounts || !accounts.length) throw new Error("No accounts");

    STATE.connected = true;
    STATE.account = accounts[0];

    const connectBtn = els.connectBtn();
    const walletEl = els.walletAddr();
    const addrText = els.walletAddrText();

    if (connectBtn) connectBtn.classList.add("hidden");
    if (walletEl) walletEl.classList.remove("hidden");
    if (addrText) addrText.textContent = shortAddr(STATE.account);

    localStorage.setItem(RECONNECT_KEY, "1");

    await applyNetworkState();
    emit("connect", { account: STATE.account });
  } catch (e) {
    console.error("[gen-escrow] connect error", e);
    alert("Failed to connect wallet: " + (e.message || e));
  }
}

export function disconnect() {
  STATE.connected = false;
  STATE.account = null;
  STATE.correctNetwork = false;

  const connectBtn = els.connectBtn();
  const walletEl = els.walletAddr();
  const addrText = els.walletAddrText();

  if (connectBtn) connectBtn.classList.remove("hidden");
  if (walletEl) walletEl.classList.add("hidden");
  if (addrText) addrText.textContent = "";

  localStorage.removeItem(RECONNECT_KEY);
  hideOverlay();
  emit("disconnect");
}

function shortAddr(a) {
  if (!a) return "—";
  return a.slice(0, 6) + "…" + a.slice(-4);
}

export async function silentReconnect() {
  const provider = eth();
  if (!provider || !localStorage.getItem(RECONNECT_KEY)) return;

  try {
    const accounts = await provider.request({ method: "eth_accounts" });
    if (accounts && accounts.length) {
      STATE.connected = true;
      STATE.account = accounts[0];

      const connectBtn = els.connectBtn();
      const walletEl = els.walletAddr();
      const addrText = els.walletAddrText();

      if (connectBtn) connectBtn.classList.add("hidden");
      if (walletEl) walletEl.classList.remove("hidden");
      if (addrText) addrText.textContent = shortAddr(STATE.account);

      await applyNetworkState();
      emit("connect", { account: STATE.account });
    }
  } catch (e) {
    console.warn("[gen-escrow] silent reconnect failed", e);
  }
}

export function bindNetworkUI() {
  if (STATE.listenersAttached) return;
  STATE.listenersAttached = true;

  const provider = eth();
  if (!provider) return;

  provider.on?.("accountsChanged", async (accounts) => {
    if (!accounts || !accounts.length) {
      disconnect();
      return;
    }
    STATE.account = accounts[0];
    const addrText = els.walletAddrText();
    if (addrText) addrText.textContent = shortAddr(STATE.account);
    await applyNetworkState();
    emit("connect", { account: STATE.account });
  });

  provider.on?.("chainChanged", async () => {
    await applyNetworkState();
  });

  // Buttons
  els.connectBtn()?.addEventListener("click", connectWallet);
  els.disconnectBtn()?.addEventListener("click", disconnect);
  els.switchBtn()?.addEventListener("click", switchToStudioNet);
  document.getElementById("overlay-dismiss")?.addEventListener("click", hideOverlay);
}
