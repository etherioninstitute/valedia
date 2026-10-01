# Burton-only Arena micro-pilot — ready checklist (2026-09-14 PT)

**Repo:** `~/MemPalace/sources/repos/github/lf-game-theory`  
**Config:** `experiments/arena-valedia-micro-pilot.yaml` (4 agents × 5 rounds × 100 sats)  
**Fund floor:** ≥ **440 sats** on operator (400 + 10% gate). Treat as burnable.  
**Fence:** You fund and run. Valedia / RuFlo / Grok Build = code/audit only — no keys in chat, no live start for you.

Prep status (already done on NIMROD under **Node 24.20.0**):
- [x] Pilot YAML written
- [x] `npm install` + `npm audit fix` (0 vulns) + `npm run build`
- [x] `better-sqlite3` loads
- [x] Dry-run `npm run arena:valedia-micro` → stops on missing `OPENAI_API_KEY` (expected)

---

## 0) Node for this repo

Use **nvm Node 24** (not Hermes Node 26 — `better-sqlite3` failed to compile there):

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use 24.20.0
cd ~/MemPalace/sources/repos/github/lf-game-theory
node -v   # expect v24.x
```

---

## 1) Lightning faucet account (Coinbase ≠ wallet login)

**Site:** https://lightningfaucet.com/ai-agents (HTTPS; upstream custody for this research).

**Login is Lightning-wallet QR auth**, not Coinbase email:
- Works with: Wallet of Satoshi, Phoenix, Zeus, Breez, BlueWallet, Alby, …
- **Coinbase app cannot scan that login QR** (no LNURL-auth).

**Practical path:**
1. Install **Phoenix** or **Wallet of Satoshi**.
2. Move a small BTC amount from Coinbase → that wallet (on-chain withdraw is the usual path; Lightning only if Coinbase offers LN to that destination).
3. Open faucet → **Lightning Wallet** tab → scan QR → wait until “Waiting for wallet…” clears.
4. Save the **recovery code** offline (password manager). Never paste it into chat/Slack/Grok.
5. Create / open the **AI Agents operator** key (`op_…`). Copy only into local `.env`.

**Risks (from audit):** third-party custody; payments **>100 sats take ~1%**; pool only shrinks; don’t park large balances.

---

## 2) Local `.env` (never paste values here)

```bash
cd ~/MemPalace/sources/repos/github/lf-game-theory
cp -n .env.example .env
chmod 600 .env
# Then open .env in an editor and replace the three placeholders.
# Required variable NAMES (values stay only in that file):
#   ANTHROPIC_API_KEY
#   OPENAI_API_KEY
#   LF_OPERATOR_KEY
# Do not put those names on the same line as cp.
```

Set Anthropic + OpenAI dashboard **spend caps** before the run.

---

## 3) Fund operator (you only)

1. Deposit **≥ 440 sats** (recommend ~500–1000 for fee + mistakes) to the faucet operator wallet.
2. Confirm balance in faucet UI before starting.
3. Do **not** fund full 16×1000 until this micro-pilot teardown is green.

---

## 4) Run the micro-pilot

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use 24.20.0
cd ~/MemPalace/sources/repos/github/lf-game-theory
npm run arena:valedia-micro
```

Watch console: Setup → Rounds 1–5 → **Teardown sweep**.  
Do **not** Ctrl-Z / kill -9 mid-run (strands agent wallets — recover via faucet UI).

---

## 5) After run

1. Confirm operator balance ≈ leftovers (minus faucet fees + agent spend).
2. Secure or delete `arena-*.db` (agent API keys live in SQLite plaintext).
3. Only then consider a larger YAML.

---

## GO/NO-GO

| Step | Status |
|------|--------|
| Code prep (Node 24 build) | **GO** |
| Funded micro-pilot ≤ ~1k sats | **CONDITIONAL GO** (you drive) |
| Full Arena 16×1000 | **NO-GO** until micro teardown green |

See also: `SEBAI-ARENA-SECURITY-AUDIT.md`, `SEBAI-ARENA-VALEDIA-PLAN.md`.
