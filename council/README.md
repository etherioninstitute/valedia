# Valedia Council of Advisors (harvested from AI Fund)

**What this is:** a local, multi-lens trading-analysis layer for Valedia. Ornith (the deep brain)
acts as **CIO** — it convenes a panel of advisor personas, feeds each one Valedia's real signals,
collects their structured views, runs them past a risk-manager gate, and synthesises a single
maximise-profit decision. MiroFish is one voice on this council (the crowd/retail lens, ≤10% weight),
not a standalone signal.

**Provenance & license:** persona prompts (`advisors/`) and quant libraries (`lib/`) were harvested
from **AI Fund** (`github.com/cubexch/ai-fund`, MIT, commit `bb9a243`), created by @seb.ai. We
**harvested the value, not the runtime**: AI Fund is built for cloud Claude Code, which would send
market data off-device — that violates Valedia's "nothing leaves this Mac" rule. So the advisors run
on **local Ornith / Valedia models only**, never cloud Claude Code. `lib/` is TypeScript reference
maths to port into Valedia's Python engine (Etherion) as needed.

## The council (45 advisors in `advisors/`)
Ornith does NOT convene all 45. For Valedia (crypto-first + Kalshi events), the standing roster:

- **risk-manager** — convened FIRST, always. Gates every idea: "what would I refuse to fund, and why."
  Maps onto Valedia's fee-gate + fund-rules + one-risk-brain.
- **quant-analyst / jim-simons / ed-thorp** — statistical edge, net-of-cost alpha, Kelly sizing, alpha-decay.
- **george-soros / stanley-druckenmiller / raoul-pal / ray-dalio** — macro / regime / reflexivity.
- **sentiment-analyst** — pairs with MiroFish's social-sim output.
- **onchain-analyst / willy-woo** — on-chain flows (whales, exchange balances).
- **market-maker / gwyneth-chen / orderflow-analyst** — microstructure, spread, liquidity.
- **volatility-analyst / funding-rate-farmer** — vol regime, perp funding.
- **portfolio-manager** — position sizing, correlation, portfolio-level risk.
- **backtester / performance-analyst** — overfit filters + ledger scoring (see `lib/backtester.ts`).

Named investor personas (warren-buffett, peter-lynch, michael-saylor, cathie-wood-crypto, arthur-hayes,
gcr, cobie, hsaka, tetranode, plan-b, jesse-livermore, paul-tudor-jones…) are optional "guest" lenses to
pressure-test a thesis. They are AI simulations of public *styles*, NOT the real people — treat their
output as a structured argument, never as advice.

## Quant libraries to port to Python/Etherion (`lib/`, highest value first)
`regime-detector`, `backtester` (overfit red-flags: Sharpe>3, WR>80%, <30 trades, >50% OOS degradation),
`indicators`, `portfolio-optimizer`, `stat-arb`, `factor-model`, `funding`, `microstructure`,
`signal-generator`, `confluence-detector`, `valuation`, `options`, `monte-carlo`, `execution-planner`.

## How Ornith uses the council (the operating loop — Phase 2 builds the automation)
1. Pull current signals, **naming the source of every figure** (never estimate): live seed
   (CoinGecko/Yahoo/Kalshi/Polymarket/F&G/news), MiroFish social-sim, Etherion risk regime, forward-test ledger.
2. Convene the relevant advisors — each returns ONE paragraph, labelled `[DATA]` vs `[AI INTERPRETATION]`.
3. **risk-manager reacts** — what to refuse to fund and why.
4. Ornith synthesises → at most 1–2 ideas, each with thesis + risks + a specific invalidation condition.
5. **Approval gate** (Burton) → paper/forward-test ledger. Nothing funded until a positive fee-inclusive edge.

## Non-negotiables (inherited from Valedia)
Local-only (Ornith, never cloud Claude Code for Burton's data). Paper/ledger until edge proven.
Approval gate on spend/send/transfer. Fee-gate. MiroFish ≤10% weight. "No trade today" is a valid answer.

See MemPalace `repos` wing for the full AI Fund analysis. Phase 2 = build the Ornith-CIO decision loop.
