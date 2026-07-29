<div align="center">

# 🛡️ GenEscrow

### Smart Escrow with AI-Powered Dispute Resolution on GenLayer

_Hold funds on-chain. Set clear terms. Let AI validators settle disputes fairly._

[![Network](https://img.shields.io/badge/Network-GenLayer%20StudioNet-2f81f7)](https://studio.genlayer.com)
[![Contract](https://img.shields.io/badge/Runner-py--genlayer%20(pinned)-4c8bf5)](https://docs.genlayer.com)
[![Tests](https://img.shields.io/badge/Tests-direct%20%2B%20integration-3fb950)](#-testing)
[![License](https://img.shields.io/badge/License-MIT-lightgrey)](#-license)

</div>

---

## 📖 Overview

Traditional escrow relies on a trusted middleman. On-chain escrow removes the middleman but can't reason about the messy reality of "did the seller actually deliver?"

**GenEscrow** closes that gap. Funds are held in an intelligent contract on **GenLayer**. Both parties agree on release terms up front. When something goes wrong, either side can raise a structured dispute — and GenLayer's validators independently run an LLM over the terms and evidence, then reach **consensus** on a fair split. No centralized arbiter, no opaque judgment.

### Why it matters

| Problem | GenEscrow's answer |
|---------|--------------------|
| Middlemen take fees and time | Funds custodied by code; near-zero overhead |
| Pure smart contracts can't judge nuance | LLM validators evaluate terms + evidence |
| One node could bias an AI decision | Multi-validator **consensus** with tolerance-based equivalence |
| Payout bugs strand funds | Pull-payment (claimable) pattern for every payout |

---

## ✨ Key Features

- **⚡ One-step create + fund** — a single payable transaction opens and funds the escrow in native GEN.
- **✅ Release / Refund** — the buyer can release to the seller or refund themselves while `FUNDED`.
- **⚖️ Structured disputes** — either party raises a dispute with a reason and supporting evidence.
- **🤖 AI consensus resolution** — validators independently judge the case via LLM and agree on the winner and split (`run_nondet_unsafe` with a custom equivalence principle).
- **⏳ Timeout claim** — the seller can claim after the deadline when no dispute is open.
- **💸 Pull-payment payouts** — all funds are credited to a claimable balance and withdrawn via `claim()`, so no payout can revert the whole transaction or strand value.
- **🔒 Mandatory network guard** — a full-screen overlay blocks interaction on the wrong chain, with auto switch/add for GenLayer StudioNet.
- **📱 Mobile-first UI** — clean, responsive, dependency-free frontend.

### Status machine

```
FUNDED ──▶ COMPLETED        (buyer releases)
       ──▶ REFUNDED         (buyer refunds)
       ──▶ EXPIRED          (seller claims after deadline)
       ──▶ DISPUTED ──▶ RESOLVED   (AI decides split / winner)
```

All monetary values are stored as `u256` in **atto** units (`value × 10^18`).

---

## 🧱 Tech Stack

| Layer | Technology |
|-------|------------|
| **Smart contract** | Python intelligent contract on **GenLayer GenVM** (pinned `py-genlayer` runner) |
| **AI / consensus** | GenLayer non-deterministic block (`gl.nondet.exec_prompt`) + custom validator equivalence |
| **Storage** | Flat on-chain storage — `TreeMap` indexes + JSON-serialized records (no nested collections) |
| **Frontend** | Vanilla JavaScript (ES modules), zero build step |
| **Chain SDK** | [`genlayer-js`](https://www.npmjs.com/package/genlayer-js) via ESM |
| **Wallet** | MetaMask / EIP-1193 provider |
| **Tooling / tests** | `genvm-lint`, `genlayer-test` (`pytest` direct mode + `gltest` integration) |
| **Network** | GenLayer **StudioNet** (chain id `61999` / `0xF22F`, gasless) |

---

## 📍 Deployed Contract

| | |
|---|---|
| **Contract address** | `0xF80C4d6b15A3Fd9943223211Aa923D8e09bd31f6` |
| **Network** | GenLayer StudioNet (`0xF22F` · 61999) |
| **Explorer / Studio** | https://studio.genlayer.com |

> The frontend is already configured for this address in [`frontend/js/contract.js`](frontend/js/contract.js).

---

## 🚀 Quick Start

### Prerequisites

- A modern browser with **MetaMask** (or any EIP-1193 wallet)
- Python **3.11+** (only needed for contract development / tests)

### Run the app

The frontend is fully static — no build, no bundler.

```bash
# Option A: open it directly
open frontend/index.html

# Option B: serve it locally (recommended)
cd frontend
python -m http.server 8080
# then visit http://localhost:8080
```

Then:

1. **Connect MetaMask.**
2. **Approve the switch to GenLayer StudioNet** — the app auto-adds the network if it's missing.
3. **Create an escrow** (seller, amount, terms, deadline) or act on an existing one — release, refund, dispute, resolve, or claim.

---

## 🧪 Testing

```bash
# 1. Lint & validate the intelligent contract
genvm-lint check contracts/genescrow.py

# 2. Fast direct-mode tests (leader-only, ~milliseconds)
pytest tests/direct/ -v

# 3. Full integration tests against live StudioNet (real LLM + validator consensus)
gltest tests/integration/ -v -s --network studionet

# Include the slow AI dispute-resolution test
gltest tests/integration/ -v -s --network studionet -m slow
```

- **Direct tests** cover business logic, validation, and state transitions.
- **Integration tests** (`tests/integration/test_lifecycle.py`) exercise every write method under real consensus and verify that value actually moves on-chain — payouts settle on transaction **finalization** and are confirmed against live balances.

---

## 📁 Project Structure

```
gen-escrow/
├── contracts/
│   └── genescrow.py          # Intelligent contract (pinned runner, flat storage)
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── app.js            # Orchestration
│       ├── contract.js       # genlayer-js read/write layer
│       ├── wallet.js         # Wallet + network guard
│       └── ui.js             # Rendering, modal, toasts
├── tests/
│   ├── direct/               # Fast leader-only tests
│   └── integration/          # Full consensus + LLM tests
├── gltest.config.yaml
├── pytest.ini
└── README.md
```

---

## 🔗 Links

- 🌐 **Live App:** _coming soon_ <!-- add deployment URL -->
- 🐦 **X / Twitter:** _coming soon_ <!-- add handle -->
- 💻 **GitHub:** _coming soon_ <!-- add repository URL -->
- 📚 **GenLayer Docs:** https://docs.genlayer.com
- 🛠️ **GenLayer Studio:** https://studio.genlayer.com

---

## 📄 License

Released under the **MIT License**.

---

<div align="center">

**Built on [GenLayer](https://genlayer.com)** — the intelligent contract platform where code can reason.

</div>
# gen-escrow
# gen-escrow
