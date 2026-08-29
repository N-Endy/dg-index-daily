# How to read this site

This page turns DataGaffer’s **DG Index** style scores and **DG Rating** strength scores into simple, directional notes about upcoming matches. It is built for everyday readers — not analysts.

## Important caveats first

- **Not betting advice.** Sports are volatile. Treat every lean as a conversation starter, not a tip.
- **Not self-learning (yet).** The engine is a transparent **rule-based** score using fixed weights, plus a simple **goal model** (Poisson) built from attack and defence ratings. It does **not** automatically retrain on yesterday’s results.
- **Percentages are model probabilities**, not “sure things.” They come from expected goals for each side — useful for comparing fixtures, not for claiming precision.
- **One lens among many.** The DG Index and DG Rating are useful context alongside form, injuries, weather, and your own judgment.
- **Data refreshes daily** (about 08:00 UTC on Railway). If the banner says data is stale, wait for the next scheduled refresh.

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
| **Corners** | Over or Under 9.5 corners |
| **Shots / SOT** | Over or Under 25.5 shots / 8.5 shots on target |
| **Cards** | Over or Under 3.5 total cards |

Open **Market details** for drivers and how our lean compares to DataGaffer’s simulation or the book (when available). Corners, shots, and cards often have no book line in the feed — treat those as exploratory.

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

## How to use this properly

1. Open the **dashboard** and pick a date / league if you want a quieter list.
2. Optionally open **Market leans** in the sidebar to combine directions — for example BTTS Yes **and** SOT Over **and** Goals 2.5 Over. Use **Match all** (default) so every pick must hold, or **Match any** if one is enough.
3. Raise **Min probability** / **Min confidence** to keep only stronger market leans. With no market picks selected, those floors apply to the main match-winner lean instead.
4. Skim **high-confidence** leans first; treat low-confidence as noise.
5. Read **match style** if you care about goals/tempo more than the 1X2 lean.
6. Compare **Our lean** with **DG model** and **Book** — agreement is a soft green flag, not a guarantee.
7. Click **Technical details** only if you want the model version and raw contribution scores.

The filter URL is shareable — copy the address bar after Apply to send someone the same shortlist. Note that corners / shots / cards probabilities are **heuristic** (style-driven), while goals and BTTS percentages come from the goal model.

## What “stale” means

If the site shows a yellow “stale” banner, the last successful refresh was more than about **36 hours** ago. Numbers may still display, but treat them cautiously until the daily job runs again.
