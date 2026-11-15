# Bonsai 2 against Qwen3.8-27B on recorded MiroFish requests

Date: 2026-11-15 (17:39 to 18:18 UTC). Asked by the owner: can Ternary Bonsai 2 replace the 27B, and have the
responses been compared? Before today there was one small test (11 November: four personas, one malformed; no
comparison of content). This is the first comparison on real requests.

## What was done

- Source: the stopped 48-agent run of 15 November (`valedia-native-20261114-swarm48-b`), which kept every request and
  the 27B's reply to it. Only requests whose input was whole were used (agent turns fed shredded input were left out).
- **Bonsai 2** (`ternary-bonsai-2-27b`, 2-bit GGUF, 8.26 GB, loaded with a 32,768 context and one slot) answered 54 of
  those requests, unchanged except for the model name: 10 personas, 5 configuration calls, 3 extraction calls,
  36 agent turns.
- **Baseline:** the 27B (`qwen/qwen3.8-27b`, 16.08 GB) answered 29 of the same 54 again. Its replies vary from run to
  run, so a difference between Bonsai 2 and the recorded reply means nothing until it is set against the 27B's
  difference from itself.
- One request at a time, through the local server, beside the live order-book collector.

## Results

| | Bonsai 2 | 27B asked again | 27B as recorded in the run |
|---|---|---|---|
| Requests answered | 54 of 54 | 29 of 29 | (all) |
| Replies cut off at the cap | 0 | 0 | 0 |
| Agent turns with a valid tool call | 36 of 36 | 20 of 20 | 36 of 36 |
| Personas that parse as strict JSON | 9 of 10 | 5 of 5 | 9 of the same 10; 39 of all 52 |
| Configuration and extraction calls valid | 8 of 8 | 4 of 4 | 8 of 8 |
| Same first action as the recorded reply, on the 20 turns both answered | 14 of 20 | 17 of 20 | |
| Turns that did nothing or only refreshed, same 20 | 6 of 20 | 3 of 20 | 0 of 20 |
| Turns that did nothing or only refreshed, all 36 | 10 of 36 | | 0 of 36 |
| Reply length of an agent turn, median tokens | 113 | 211 | 160 |
| Reading speed, tokens a second | 259 | 260 | |
| Writing speed, tokens a second | 21.8 | 20.3 | |
| Seconds for an agent turn, median | 14.1 | 20.1 | |
| Seconds for a persona, median | 51.4 | 41.6 | |

## What this shows

- **Bonsai 2 is usable for this work.** Every tool call was valid, nothing was cut off, and its posts read as the same
  persona on the same subject (samples below).
- **It is not faster.** Reading and writing speeds are the same as the 27B's. Its agent turns finished sooner only
  because its posts are shorter.
- **A malformed persona is not a Bonsai fault.** The 27B's own personas fail a strict parse 13 times in 52 in the
  recorded run; the application repairs them. Bonsai 2's one in ten here, and one in four on 11 November, sit inside
  that rate.
- **It may be more passive.** It chose to do nothing or to refresh in 6 of 20 turns against the 27B's 3 of 20 when
  asked again. With 20 turns that difference is not established. The recorded run had none in 36, so the 27B asked
  again through the plain server is itself more passive than it was inside the run, which is unexplained (different
  sampling settings are the first suspect).
- **Memory:** with Bonsai 2 loaded and the 27B unloaded, free memory was 46% (median) and 29% at its lowest, beside the
  collector's model. With the 27B loaded instead it was about 29%, lowest 22%. Bonsai 2 leaves roughly 10 GB more
  free. The two were not loaded alike (the 27B had its usual 262,144 context and four slots).

## What it does not show

- Prompts here were at most about 3,900 tokens. The repaired swarm will send prompts several times longer, and a
  2-bit model may hold up less well there. Not tested.
- The report phase was not tested (no report request was recorded).
- 36 agent turns and 10 personas are small samples. Differences of a few turns are noise.
- Nothing here ran inside the MiroFish pipeline. The pipeline is pinned to the 27B's exact files (tokeniser, prompt
  template, hashes); using another model there means qualifying it again, not changing a setting.

## What it means for the swarm

Swapping in Bonsai 2 would not shorten a swarm run, because the time goes on reading prompts and both models read at
the same speed. Its gain is memory. That is not enough, as the pipeline's rules stand, to keep the order-book
collector running during a swarm: the pipeline wants 65% of memory free at its start, and Bonsai 2 beside the
collector's model left 46%.

## The machine during the test

- The first attempt stopped itself after 12 requests at 20% free memory: Bonsai 2, the collector's model and the idle
  resident 27B do not fit together. The 27B was unloaded for the rest of the Bonsai part and loaded again for the
  baseline part; it is loaded now as it was before.
- The order-book collector made 118 decisions in the window and lost none. Its answers took about 4.0 seconds
  (median) instead of 3.5, the slowest 9.6 seconds.
- Scripts and raw replies: `/Volumes/NIMROD_SSD/claude-tmp/valedia-trader1/bonsai-replay/`.

## Replies side by side (clipped)

### Personas

**Call dc2b7a03**

- 27B, as recorded in the run: { "bio": "Institutional bond strategist focused on European sovereign and corporate debt. Analyzing yield curve dynamics, credit spreads, and macroeconomic implications for long-term institutional portfolios.", "persona": "Greta Halvorsen operates as a specialized institutional account representing the analytical voice of a senior bond strategist within a Nordic asset management framework, likely associated with entities such as Workers Pension Fund or similar large-scale European institutional investors. The primary function of this digital persona is to provide high-level market commentary, strategic outlooks on fixed-income instruments, and educational content regarding interest rate envi ...
- 27B, asked again: { "bio": "Greta Halvorsen is a Senior Bond Strategist at the Workers Pension Fund, specializing in interest rate risk, credit spreads, and long-term liability matching. Focused on prudent, long-term capital preservation.", "persona": "Greta Halvorsen is the official digital persona representing the bond strategy desk of the Workers Pension Fund, a large-scale institutional investor dedicated to securing the financial futures of public sector employees. The Workers Pension Fund was established to manage the collective assets of millions of workers, with a mandate that prioritizes stability, transparency, and long-term growth over short-term speculation. Greta Halvorsen, as the face of this de ...
- Bonsai 2: { "bio": "Greta Halvorsen, Bond Strategist. Analyzing fixed-income markets, yield curves, and institutional risk. Supporting Workers Pension Fund and collaborative research with Hollin Fold Systematic.", "persona": "Greta Halvorsen operates as the primary strategic voice for a specialized bond strategy desk, functioning within the broader ecosystem of institutional asset management. The entity is not a traditional corporate headquarters but rather a highly specialized analytical unit dedicated to fixed-income markets, specifically focusing on the intersection of long-duration risk and institutional mandates such as those held by the Workers Pension Fund. Founded through a series of collabora ...

**Call 20839fd4**

- 27B, as recorded in the run: { "bio": "Official risk oversight channel for institutional portfolio management. Monitoring systemic exposure, liquidity constraints, and sovereign debt dynamics. Data-driven insights on capital preservation strategies.", "persona": "Farid Nouri operates as the designated Risk Officer account within a complex network of financial institutions, specifically associated with the Workers Pension Fund and broader pension management entities. The official name of this entity is Farid Nouri, functioning not merely as an individual but as the digital avatar for institutional risk governance protocols. Founded in the context of modernizing pension fund transparency and accountability, the primary fu ...
- 27B, asked again: { "bio": "Farid Nouri | Chief Risk Officer, Workers Pension Fund. Stewarding $4.2B in public wealth. Focused on sovereign debt, inflation hedging, and long-term capital preservation. #RiskManagement #PublicFinance", "persona": "Farid Nouri serves as the Chief Risk Officer for the Workers Pension Fund, a large-scale institutional investor responsible for managing the retirement assets of federal and state public employees. The account operates under the professional identity of the fund's risk division, presenting a formal, authoritative, and data-driven persona that reflects the gravity of public fiduciary duties. The organizational nature is that of a non-profit public trust, founded to ens ...
- Bonsai 2: { "bio": "Risk Officer at Workers Pension Fund. Providing expert insights on pension risk management, sovereign debt monitoring, and institutional market stability. #PensionFund #RiskManagement", "persona": "Farid Nouri operates as the designated Risk Officer for the Workers Pension Fund, a specialized institutional entity dedicated to safeguarding long-term retirement assets. The account functions as an authoritative voice within the broader financial ecosystem, representing the prudent and conservative end of institutional decision-making. Founded on principles of fiduciary duty and actuarial prudence, the Workers Pension Fund serves millions of workers across various sectors, managing tri ...


### Agent turns

**Call 489f78b7**

- 27B, as recorded in the run: create_post {"content":"Thought Leadership: On the Misunderstanding of Systematic Trend-Following by Retail Participants.\n\nWe frequently encounter inquiries from individual investors seeking \"hot tips\" or real-time trade signals from our platform. This post clarifies our operational philosophy for the institutional audience we ...
- 27B, asked again: create_post {"content":"Thought Leadership: Why \"Predictions\" Are the Wrong Framework for Institutional Trend-Following\n\nIn conversations with LPs and alternative investment analysts, we encounter a persistent misconception: that systematic trend-following strategies should be evaluated on their ability to \"predict\" the next ...
- Bonsai 2: create_post {"content":"Thought Leadership: On the Economics of Volatility as Opportunity Cost\n\nInstitutional LPs frequently frame volatility as a threat to be minimized. Our research culture at Ironvale holds a different view: volatility is an opportunity cost that must be compensated by trend capture.\n\nWhen markets are calm, ...

**Call f72bdfbf**

- 27B, as recorded in the run: create_post {"content":"Pre-market thoughts: No major catalysts on the docket today, which means we're likely in a liquidity-driven environment again. I'm watching the 20-day EMA on SPY as my primary reference — if price holds above it into the first hour, I'll treat any pullback toward VWAP as a potential entry zone with stops be ...
- 27B, asked again: create_post {"content":"Pre-market check for today's session:\n\nSPX futures pointing up ~0.3% but I'm watching two things closely:\n\n1) Volume profile — if we see a thin tape on the open, I'm staying flat. Low-volume pushes don't hold and often reverse by 10:30 ET.\n\n2) The 200-day MA on QQQ — sitting right at it. If we reject  ...
- Bonsai 2: create_post {"content":"Pre-market thoughts: The overnight futures tape is telling us a story of cautious positioning ahead of today's open. I'm watching the 20-day moving average on the S&P 500 as my primary liquidity reference — if we hold above it, I'm looking for continuation; if we lose it with volume, that's my stop-loss dis ...

**Call c53bd9f4**

- 27B, as recorded in the run: create_post {"content":"Market commentary, 09:30 CET.\n\nPre-open positioning in Stockholm and London equity desks reflects a measured stance heading into the mid-week session. The SEK has firmed modestly against the EUR on the back of revised CPI prints from Statistiska Centralbyrån, which came in 0.2% below consensus at 2.8% yea ...
- 27B, asked again: create_post {"content":"Wednesday analytical note, 09:00 CET.\n\nThe Aldgrave Strategic Fund's Q3 earnings release has generated a predictable cycle of market commentary, but the signal buried within the noise deserves institutional attention. Their reallocation towards emerging market equity—initially met with considerable skepti ...
- Bonsai 2: create_post {"content":"Wednesday analytical piece, 08:30 CET.\n\nA critical examination of the Aldgrave Strategic Fund's emerging markets repositioning, now two years into its mandate shift. The initial skepticism from institutional peers has been substantially vindicated by performance data, yet the narrative surrounding this tr ...

**Call 9edc954b**

- 27B, as recorded in the run: create_post {"content":"The Workers Pension Fund's tilt toward green infrastructure bonds is being framed as a \"values play\" in some commentary. Let's be precise: it's a yield-duration arbitrage dressed up as ESG rhetoric. The 7-10y tranches are offering a 42bp spread over conventional sovereigns with comparable credit quality.  ...
- 27B, asked again: do_nothing {}
- Bonsai 2: create_post {"content":"A note on the Workers Pension Fund's green infrastructure allocation shift: the 2019 liquidity crisis taught us that \"strategic\" reallocation narratives can mask underlying repo stress. The data suggests this isn't a cyclical rebalancing—it's a structural repositioning of sovereign-grade credit exposure.  ...

**Call c0b7fab9**

- 27B, as recorded in the run: create_post {"content":"The Calder Bay Public Employees Fund Board of Trustees has finalized its Q3 2024 investment allocation review, reaffirming the fund’s commitment to a diversified long-term strategy anchored in actuarial integrity and intergenerational equity.\n\nKey points from this quarter’s stewardship update:\n\n• Portfo ...
- 27B, asked again: create_post {"content":"The Calder Bay Public Employees Fund has published its quarterly stewardship update, confirming that portfolio allocations remain within the approved risk framework and that actuarial funding levels continue to support our long-term obligations to the public servants who serve this region.\n\nKey highlights ...
- Bonsai 2: do_nothing {}

