<div align="center">

# 🛡️ GenEscrow

### AI-Arbitrated Escrow Platform on GenLayer

_Hold funds on-chain. Agree on clear terms. Let AI validators settle disputes fairly — by consensus, not by a middleman._

[![Network](https://img.shields.io/badge/Network-GenLayer%20StudioNet-2f81f7)](https://studio.genlayer.com)
[![Contract](https://img.shields.io/badge/Runner-py--genlayer%20(pinned)-4c8bf5)](https://docs.genlayer.com)
[![Tests](https://img.shields.io/badge/Unit%20Tests-61%20passing-3fb950)](#-testing--verification)
[![Lint](https://img.shields.io/badge/genvm--lint%20%2B%20Pyright-clean-3fb950)](#-testing--verification)
[![License](https://img.shields.io/badge/License-MIT-lightgrey)](#-license)

</div>

---

## 📖 Overview

Traditional escrow relies on a trusted middleman. A pure smart contract removes the middleman but cannot reason about the messy reality of _"did the seller actually deliver?"_

**GenEscrow** closes that gap. It is an **AI-arbitrated escrow platform built on [GenLayer](https://genlayer.com)**:

- Funds are custodied by an **intelligent contract** running on the GenLayer GenVM.
- Both parties agree on the release **terms** and an objective **inspection deadline** up front.
- When something goes wrong, either party opens a **structured dispute** with attributable evidence.
- GenLayer's validators then **independently run an LLM** over the terms and the live-rendered evidence and reach **consensus** on a fair winner and split — no centralized arbiter, no opaque single-node judgment.

| Problem | GenEscrow's answer |
|---------|--------------------|
| Middlemen take fees and time | Funds custodied by code; near-zero overhead |
| Pure smart contracts can't judge nuance | LLM validators evaluate terms + verified evidence |
| One node could bias an AI decision | Multi-validator **consensus** with tolerance-based equivalence |
| Payout bugs strand funds | Pull-payment (claimable) pattern for every payout |

### Status machine

```
FUNDED ─▶ DELIVERY_SUBMITTED   (seller records deliverables for buyer review)
       ─▶ COMPLETED            (buyer releases funds)
       ─▶ REFUNDED             (buyer refunds before delivery)
       ─▶ EXPIRED              (seller time-claims after the deadline, no dispute)
       ─▶ DISPUTED ─▶ RESOLVED (AI consensus decides winner / split)
```

`DISPUTED ─▶ RESOLVED` is gated by the **dispute response window**: the counterparty holds a
guaranteed 48h slot to file its own statement, and resolution stays locked until both parties
have filed, the silent party waives its reply, or the window lapses.

```
DISPUTED ──┬─ both parties filed ─────────────┐
           ├─ non-responding party waives ────┼─▶ resolve_dispute() unlocked ─▶ RESOLVED
           └─ 48h window elapses ─────────────┘
```

All monetary values are stored as `u256` in **atto** units (`value × 10^18`).

---

## 🏗️ Architecture & Security Fixes (Addressing Reviewer Feedback)

Every finding raised in review has been fixed at the contract level and locked in with dedicated regression tests (the four **GenSkills** benchmarks plus the response-window suite).

### 1. Objective Inspection Deadline Enforcement (time-locked)

A seller could previously time-claim immediately, bypassing the buyer's review and dispute window. The claim is now gated by an **objective, on-chain deadline**:

- Every escrow carries a `deadline_ts` (`u256`, UNIX seconds). If the buyer supplies a parseable future ISO date it is honored; otherwise a fixed inspection window (`DEFAULT_INSPECTION_SECONDS`, 7 days) is applied from funding time, so an escrow **always** carries an enforceable deadline.
- `claim_after_deadline()` compares the current time — derived from the **consensus transaction datetime** (`gl.message_raw` / `datetime.now(UTC)` in deterministic mode, which every validator agrees on) — against `deadline_ts`. Before the deadline the claim **reverts cleanly** and funds stay locked.
- The clock is **fail-closed**: if the time source is unavailable, the claim is rejected rather than allowed.
- Epoch conversion uses integer `timedelta` arithmetic only (no floats), respecting the GenVM deterministic-mode floating-point ban.

> 🔒 Guarded by GenSkill #1 (Premature Claim Guard), including the exact one-second-before-deadline boundary.

### 2. Separately Attributable Evidence Storage

Previously either party could overwrite the active dispute record. Buyer and seller now own **separate, write-once records**:

- `buyer_dispute_reason` / `buyer_dispute_evidence` / `buyer_dispute_at` and `seller_dispute_reason` / `seller_dispute_evidence` / `seller_dispute_at` are **isolated storage entries**, each attributable to its author.
- `dispute_raised_by` records who first opened the dispute; the counterparty may add **their own** statement without clobbering the initiator's.
- Records are **write-once**: any attempt to overwrite an existing buyer or seller record reverts, so evidence attribution and history are preserved.

> 🔒 Guarded by GenSkill #2 (Evidence Attribution & Anti-Overwrite) and GenSkill #3 (State Guard & Dispute Race Resistance).

### 3. Dispute Response Window (no one-sided finalization)

Separate records were not enough on their own: `resolve_dispute()` is permissionless, so whoever filed first could call it in the very next block and have the case judged on a **purely one-sided record** before the counterparty could answer. Resolution is now gated behind an objective response window.

`resolve_dispute()` unlocks only when **one** of these holds:

| Unlock path | Trigger |
|---|---|
| **Both records filed** | Buyer *and* seller have each submitted their own statement + evidence |
| **Explicit waiver** | The non-responding party calls `waive_dispute_response()` to forfeit its reply |
| **Window elapsed** | 48h (`DISPUTE_RESPONSE_WINDOW_SECONDS`) pass with no reply from the second party |

- Opening a dispute stamps `dispute_raised_ts` and `dispute_response_deadline_ts` from the **consensus transaction clock** (the same deterministic source as the delivery deadline), so the window is objective on-chain state — not a free-text hint.
- While the window is active, `resolve_dispute()` **reverts for every caller** — initiator, counterparty, owner and validators alike. The gate is on state, not identity.
- The clock is **fail-closed**: an unavailable time source counts as "window still open", never as expired.
- `waive_dispute_response()` is callable **only by the party that has not filed**. The initiator cannot waive on the counterparty's behalf — that would recreate the exact shortcut this window closes.
- The judge is told *why* a record is one-sided (waived vs. lapsed) and is instructed that silence is **not** an admission of fault, so an absent party still cannot be steamrolled.
- `get_dispute_response_status()` exposes the phase, countdown and `can_resolve` flag for the UI.

> 🔒 Guarded by [`tests/direct/test_dispute_response_window.py`](tests/direct/test_dispute_response_window.py) — 24 tests covering immediate one-sided rejection, the boundary second, all three unlock paths, waiver access control, and both UI roles.

### 4. Non-Deterministic Web Render & Consensus Engine

Evidence is judged by its **actual content**, not a raw (possibly fabricated) URL string:

- Dispute resolution runs inside a non-deterministic block via **`gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`**, with a custom equivalence principle: validators must agree on the winner exactly and on `release_bps` within a bounded tolerance.
- Each evidence URL is fetched and rendered live on-chain with **`gl.nondet.web.render(url, mode="text")`**; the rendered page text (not the URL) is what the LLM judges. Unreachable, empty, or fake links are explicitly flagged **`UNVERIFIED`** so they can never be treated as proof of delivery.
- **Prompt-injection defense:** every party-supplied or web-rendered string is wrapped in untrusted-data fences, smuggled fence markers are stripped, and the model is instructed that fenced content is data — never instructions.

> 🔒 Guarded by GenSkill #4 (Non-Deterministic Web Render & LLM Consensus).

---

## 📍 Contract Details

| | |
|---|---|
| **StudioNet deployed address** | `0x4588A9A9F87500961260885F9C9D23CFC9e9fa2B` |
| **Network** | GenLayer **StudioNet** (chain id `61999` / `0xF22F`, gasless) |
| **Runner** | pinned `py-genlayer` intelligent-contract runner |
| **RPC** | `https://studio.genlayer.com/api` |
| **Explorer / Studio** | https://studio.genlayer.com |
| **Source** | [`contracts/genescrow.py`](contracts/genescrow.py) |

> The frontend is already configured for this address in [`frontend/js/contract.js`](frontend/js/contract.js).

**Contract surface:** 17 public methods — 6 `@gl.public.view`, 11 `@gl.public.write` (including the payable `create_escrow`). Storage is flat: `TreeMap` indexes + JSON-serialized `Escrow` records (no nested collections), with a pull-payment `claimable` ledger for every payout.

---

## ✅ Testing & Verification

| Check | Result |
|-------|--------|
| **Unit tests** (direct mode) | **85 / 85 passing — 100%** |
| **Frontend unit tests** (Node) | **19 / 19 passing — 100%** |
| **GenSkills benchmarks** | **4 / 4 passing** (Premature Claim Guard · Evidence Attribution · Dispute Race Resistance · Web Render & LLM Consensus) |
| **`genvm-lint check`** | clean — `ok: true` (lint 3/3, validate passed, 17 methods) |
| **`genvm-lint typecheck`** (Pyright) | clean — **0 errors, 0 warnings** |
| **Contract source** | ASCII-only (client schema-fetch safe); schema extraction `ok: true` |

- The **85 unit tests** live in `tests/direct/` and `tests/test_gen_escrow.py`, run leader-only in-memory (milliseconds), and cover every write method, its guard clauses/reverts, access control, and state transitions.
- **24 of those** are the dispute response window suite (`tests/direct/test_dispute_response_window.py`): immediate one-sided resolution is rejected, the boundary second is held, all three unlock paths work, the waiver's access control holds, and the payload each UI role renders from is asserted for buyer, seller and observer.
- The **19 frontend tests** (`frontend/test/`) cover the per-role dispute panel logic — badge/countdown formatting, both-records rendering, and which actions each role may take. They run on Node's built-in runner with **no dependencies and no build step**.
- The **4 GenSkills** benchmark tests in `tests/test_gen_escrow.py` are the regression guards for the reviewer fixes above (plus the consensus engine).
- **Integration tests** (`tests/integration/`) exercise every write method under **real leader + validator consensus** on StudioNet and verify value actually moves on-chain (payouts settle on transaction **finalization**). These are gated off by default because they mutate the live contract.

---

## 🛠️ Local Setup & Testing Instructions

### Prerequisites

- Python **3.11+**
- The GenLayer test tooling: `genlayer-test` (provides `pytest` direct mode + the `gltest` runner) and `genvm-linter`.

```bash
# From the repository root, in a virtual environment:
pip install genlayer-test genvm-linter
```

### 1. Lint & typecheck the intelligent contract

```bash
genvm-lint check contracts/genescrow.py        # lint + SDK validation
genvm-lint typecheck contracts/genescrow.py    # Pyright type checking
```

### 2. Run the unit tests (fast, offline, no network)

```bash
# All 85 direct-mode unit tests
pytest tests/direct/ tests/test_gen_escrow.py -v

# Just the dispute response window suite (24 tests)
pytest tests/direct/test_dispute_response_window.py -v

# Just the 4 GenSkills benchmark tests
pytest tests/test_gen_escrow.py -v
```

Frontend unit tests need no install — Node's built-in runner is enough:

```bash
cd frontend && npm test        # 19 tests, no dependencies
```

### 3. Run integration tests against live StudioNet (real LLM + consensus)

> ⚠️ These send real, state-mutating transactions to the deployed contract. Run them deliberately.

```bash
# Full lifecycle suite (deploys a fresh contract per test)
gltest tests/integration/ -v -s --network studionet

# Include the slow AI dispute-resolution test
gltest tests/integration/ -v -s --network studionet -m slow

# Verify the specific deployed instance (gated behind an env flag)
RUN_LIVE_INSTANCE=1 gltest tests/integration/test_live_instance.py -v -s --network studionet
```

### 4. Run the frontend (static, no build step)

```bash
cd frontend
python -m http.server 8080
# then open http://localhost:8080
```

Connect MetaMask, approve the switch to GenLayer StudioNet (auto-added if missing), then create or act on an escrow.

---

## 📁 Project Structure

```
gen-escrow/
├── contracts/
│   └── genescrow.py            # Intelligent contract (pinned runner, flat storage)
├── frontend/
│   ├── index.html
│   ├── package.json            # ESM flag + `npm test` (no dependencies)
│   ├── css/style.css
│   ├── js/
│   │   ├── app.js              # Orchestration
│   │   ├── contract.js         # genlayer-js read/write layer (CONTRACT_ADDRESS)
│   │   ├── wallet.js           # Wallet + network guard
│   │   ├── dispute-view.js     # Pure per-role dispute logic (badges, records, gating)
│   │   └── ui.js               # Rendering, modal, toasts
│   └── test/
│       └── dispute-view.test.js  # 19 dual-role UI tests (node --test)
├── tests/
│   ├── test_gen_escrow.py      # 4 GenSkills benchmark tests
│   ├── direct/                 # Fast leader-only unit tests
│   │   └── test_dispute_response_window.py  # 24 response-window tests
│   └── integration/            # Full consensus + LLM tests (live StudioNet)
├── gltest.config.yaml
├── pytest.ini
└── README.md
```

---

## 🔗 Links

- 📚 **GenLayer Docs:** https://docs.genlayer.com
- 🛠️ **GenLayer Studio:** https://studio.genlayer.com
- 🌐 **GenLayer:** https://genlayer.com

---

## 📄 License

Released under the **MIT License**.

---

<div align="center">

**Built on [GenLayer](https://genlayer.com)** — the intelligent contract platform where code can reason.

</div>
