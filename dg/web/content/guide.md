# How to read this site

This page turns DataGaffer’s **DG Index** style scores and **DG Rating** strength scores into simple, directional notes about upcoming matches. It is built for everyday readers — not analysts.

## Important caveats first

- **Not betting advice.** Sports are volatile. Treat every lean as a conversation starter, not a tip.
- **Not self-learning (yet).** The engine is a transparent **rule-based** score using fixed weights, plus a simple **goal model** (Poisson) built from attack and defence ratings. It does **not** automatically retrain on yesterday’s results.
- **Percentages are model probabilities**, not “sure things.” They come from expected goals for each side — useful for comparing fixtures, not for claiming precision.
- **One lens among many.** The DG Index and DG Rating are useful context alongside form, injuries, weather, and your own judgment.
- **Data refreshes on a Nigerian (WAT) schedule** on Railway: matches / Strongest / AI Picks at **00:00** and **05:00**; Flashscore scores at **06:00**, **16:00**, **18:00**, **20:00**, and **22:00**. If the banner says data is stale, wait for the next scheduled refresh.

## What each column means

### Our lean

A directional call: **Favours Home**, **Favours Draw**, or **Favours Away**, with a **percentage** when available.  
It blends DG Rating strength (who is stronger) with DG Index style (how the game might play).

### Strength line

Each fixture shows both teams’ **DGRtg** (overall power rating) and a plain edge phrase — for example “clear home edge” or “evenly matched.” Large gaps usually matter more for match-winner and handicap-style reads; small gaps push attention toward totals and BTTS.

### Confidence

- **High** — several signals agree, history and consistency look solid, and the probability margin is clear.
- **Medium** — a moderate edge; still easy to be wrong.
- **Low** — mixed or thin signals; skim past these first.

### Match style

How the game might *feel*, not who wins:

- **Open** — more attacking volume; goals more likely.
- **Tight** — more control; fewer clear chances.
- **Volatile / chaotic** — intensity and swings; harder to predict the scoreline.

### DataGaffer model vs betting market

- **DataGaffer model** — DataGaffer’s own simulation probabilities for that fixture.
- **Betting market** — which side the bookmaker prices as favourite (lowest odds).

When **Our lean**, **DG model**, and **the book** agree, that is more interesting than when they disagree. Disagreement is normal — it means models and markets see different things.

### Final scores (completed matches)

After a match has finished, the board shows the full-time score when a result has been synced:

- **Flashscore.mobi** scrape on the scheduled `sync-scores` job (6am / 4pm / 6pm / 8pm / 10pm WAT — same pattern as MatchPredictor, no API key)
- Fallback: **football-data.co.uk** CSV backfill (often 1–2 days behind on weekends)
- Optional: **API-Football** leftovers if `API_FOOTBALL_KEY` is set and the account is healthy

When a score is present you will see:

- **Final** scoreline next to kickoff (e.g. Final 2–1)
- **Lean hit / Lean miss** on the match-winner lean
- **Hit / Miss** on market chips when that market can be labelled from the result (goals, BTTS, etc.)

Past kickoff with no result yet shows **Awaiting score** until the next successful sync. Flashscore markup or Cloudflare challenges can temporarily block scrapes — the job cools down and retries later.

When a scrape looks **close** but not close enough for auto-attach, a **!** appears next to Awaiting score. Click it to review possible Flashscore rows and confirm the right one (requires unlocking with `SCORE_LINK_SECRET` via `/score-link/unlock?token=…`).

### Why (key drivers)

Short plain-English reasons pulled from the biggest contributors to the score — for example “One side has a clear DG Rating strength advantage.”

### Markets (chips)

Besides the match winner lean, each fixture shows **core market lines** (still rule-based, not trained). Goals-related chips use the goal model percentage; corners / shots / cards stay style-driven:

| Chip | Meaning |
|------|---------|
| **Goals 2.5** | Over or Under 2.5 total goals |
| **Goals 3.5** | Over or Under 3.5 total goals |
| **BTTS** | Both teams to score — Yes or No |
| **Home 1.5 / Away 1.5** | That team Over or Under 1.5 goals |
| **FH 1X2** | First-half result lean |
| **FH 0.5** | First-half Over or Under 0.5 goals |
| **Corners** | Over or Under — chip shows the **selected line** (e.g. Corners 10.5) |
| **Shots / SOT** | Over or Under — chip shows the **selected line** (e.g. Shots 26.5, SOT 9.5) |
| **Cards** | Over or Under 3.5 total cards |

For corners, shots, and shots-on-target there is no book line in the feed, so the engine picks the ladder line where the DG simulation is most decisive **within a probability band** (roughly 15–85%). That favours readable leans, not betting value. The stored `line` is what we grade against once results arrive.

Open **Market details** for drivers and how our lean compares to DataGaffer’s simulation or the book (when available). When only the DG model is available you may see **DG model only** instead of **Model + book agree** — that means no book line existed for that market, not that two independent sources confirmed the lean.

## What DG Rating means

DataGaffer publishes a separate **strength** table alongside the style indexes:

| Field | Plain meaning |
|-------|----------------|
| **DGRtg** | Overall power rating — higher ≈ stronger team. |
| **ORtg / DRtg** | Attack and defence rates (roughly goals for / against per match). |
| **Home / Away rating** | Venue-specific strength (away performances often weighted more heavily on the site). |
| **Consistency** | How stable the profile has been; higher ≈ cleaner read. |
| **Luck** | Results vs underlying chance profile — a second opinion, not the main driver. |

We use ORtg and DRtg to estimate expected goals for each side in a matchup, then turn that into probabilities for the winner and goal markets. Style indexes (pace, NEC, AGIX, control, pressing) still shape how we read the *type* of game.

## What the DG Index metrics mean

Each team gets five index scores (roughly 0–100, higher is usually “more of that trait” except as noted):

| Metric | Plain meaning |
|--------|----------------|
| **PPDA index** | Pressing intensity. Higher index ≈ more aggressive pressing (raw PPDA is inverted). |
| **Pace** | How open / attack-heavy the team’s games tend to be. |
| **AGIX** | Early-game aggression and tempo. |
| **NEC** | Team scoring pressure — how much of a goal-friendly environment they create. |
| **TCIX** | Control — possession, territorial dominance, ability to dictate tempo. |

We also use **home vs away** splits of these numbers so a team’s home profile is compared to the opponent’s away profile.

## Strongest leans (today only)

The **Strongest leans** page posts **at most one** outcome per fixture for the **current Nigerian (WAT) day**. It scores every published market plus the match-winner lean, then keeps a pick only if it clears a conservative bar:

- **High** confidence
- Model probability **≥ 65%** (missing probability fails — unlike the dashboard filters)
- When DG and/or book signals exist for that market, the lean must **agree with every present signal** (both when both exist)

Fixtures that fail the bar are **omitted**. Goal-model markets are preferred over style-only lines (corners / shots / cards) when both qualify. When two leans tie, a pick with **both** DG and book agreement ranks above **DG model only**. A banner at the top summarises recent hit-rate where football-data.co.uk stats allow grading — many leagues only have full-time scores, so treat that figure as indicative, not exhaustive.

This still **does not eliminate risk** and is **not betting advice** — it is a stricter shortlist of the same rule-based leans.

## AI Picks

**AI Picks** takes today’s top gate-passing market candidates per fixture (not only the single Strongest lean) and runs a second screen with an LLM (OpenAI-compatible; default model is configurable). The model returns a coherence judgment and optional concerns, plus a publish/skip verdict. An **estimated hit chance** (shown as Est. N%) is computed from measured hit rates keyed by **market × source agreement × probability band**, shrunk toward parent aggregates when a bucket is thin, then nudged by the AI screen. It is **not** the model lean percentage (which is often overconfident). Only picks at or above the configured floor (default **55%**) are published.

- The AI may only use the fields we send (probability, confidence, DG/book agreement, drivers). It should **not** invent injuries or lineups.
- No API key → the page explains setup; the matches job stays green.
- AI notes are short plain English. The model can be **wrong** — treat this as a stricter filter, not a tip sheet.
- Each card’s **Basis** line shows the historical hit rate that anchors the estimate (market, agreement tier, and probability band) and the AI screen coherence.

## How to use this properly

1. Open **Strongest leans** for today’s high-bar shortlist, **AI Picks** for the LLM-vetted subset, or the **dashboard** for the full board (defaults to **today in WAT**; choose **All dates** for the full archive).
2. Optionally open **Market leans** in the sidebar to combine directions — for example BTTS Yes **and** SOT Over **and** Goals 2.5 Over. Use **Match all** (default) so every pick must hold, or **Match any** if one is enough.
3. Raise **Min probability** / **Min confidence** to keep only stronger market leans. With no market picks selected, those floors apply to the main match-winner lean instead.
4. Skim **high-confidence** leans first; treat low-confidence as noise.
5. Read **match style** if you care about goals/tempo more than the 1X2 lean.
6. Compare **Our lean** with **DG model** and **Book** — agreement is a soft green flag, not a guarantee.
7. Click **Technical details** only if you want the model version and raw contribution scores.

The filter URL is shareable — copy the address bar after Apply to send someone the same shortlist. Note that corners / shots / cards probabilities are **heuristic** (style-driven), while goals and BTTS percentages come from the goal model.

## What “stale” means

If the site shows a yellow “stale” banner, the last successful refresh was more than about **36 hours** ago. Numbers may still display, but treat them cautiously until the next matches job runs. Open **Status** (`/status`) for the last pipeline run, snapshot age, score-source counts, and today’s AI pick count.

Hit-rate and lean grading depend on which leagues **football-data.co.uk** publishes stat columns for (corners, shots, cards, etc.). Leagues with only full-time scores can still grade 1X2 and goals markets but not every prop line.
