# DataGaffer UI/UX Modernization Plan: High-Performance Tactical Terminal

## 1. Overview & Vision
Transform DataGaffer's matchday press sheet web interface into a **High-Performance Tactical Terminal**. 
A razor-sharp, data-dense, dark command center engineered for football analysts, sharp bettors, and quants. 

### Core Aesthetic Direction:
- **Canvas:** Deep OLED black / dark void (`#080a0f`, `#0d1117`, `#161b22`, `#21262d`).
- **Telemetry Accents:** Pitch-green phosphor (`#00e676` / `#059669`) for positive value / high probability, warm amber telemetry (`#f59e0b` / `#d97706`) for caution / high-bar leans, sharp electric coral (`#f43f5e`) for danger / splits, crisp slate (`#94a3b8`) for secondary metrics.
- **Strict Compliance:** Zero purple/violet tones (adheres strictly to Purple Ban), zero generic AI bento/mesh gradient clichés.
- **Typography:** Barlow Condensed (tight, impactful matchday headers) paired with IBM Plex Sans / JetBrains Mono (precision tabular metrics, telemetry badges).

---

## 2. Design Commitments & Architecture

### 🎨 Design Commitment (Anti-Safe Harbor)
- **Radical Style:** High-Performance Tactical Terminal (Opta / Bloomberg Sports Intelligence).
- **Topological Choice:** Dense Command Center with a sticky live telemetry HUD, collapsible league accordions, inline probability micro-gauges, and high-density market matrices.
- **Risk Factor:** Replaced generic spacious light cards with dark OLED precision HUD modules featuring micro data-bars and glow accents.
- **Readability Conflict:** Retained strict WCAG 2.1 AA contrast (>7:1 on text) with high-legibility monospace numbers and instant visual hierarchy.
- **Cliché Liquidation:** No Bento grids, no mesh/aurora gradients, no generic pastel cards, no AI buzzwords.

---

## 3. Implementation Phases

### Phase 1: Design Tokens & CSS Architecture (`dg/web/static/styles.css`)
- Rebuild CSS custom properties with dark OLED theme tokens.
- Implement telemetry badges, pulse dots, micro probability bars, and glowing status indicators.
- Responsive container layouts (desktop command center, tablet dual-rail, mobile thumb-friendly stack).
- Micro-interactions: 60fps card hover lifts, smooth accordion transitions, high-contrast focus rings.

### Phase 2: Core Layout & Navigation (`dg/web/templates/base.html`)
- Modern dark terminal header with live telemetry pulse (status indicator).
- Refined navigation with active pill tabs and shortcut guides.
- Improved accessibility (skip link, ARIA landmarks, mobile navigation drawer).
- Enhanced typography imports (Barlow Condensed + JetBrains Mono + IBM Plex Sans).

### Phase 3: Matchday Dashboard (`dg/web/templates/dashboard.html` & `_team_matchup.html`)
- Sticky HUD summary bar: generation timestamp (WAT), active league count, total fixtures, edge count, and scoring environment warning.
- Quick filter tape: fast league filter, market selector, confidence pill toggles.
- Fixture cards:
  - Club crests with high-contrast club typography and home/away matchup line.
  - Model expected goals (xG) split bar and win/draw/away probabilities.
  - Market Matrix: 1X2, Goals 2.5, BTTS, Double Chance with clear color-coded edge/lean indicators.
  - Final score / live result ribbon with hit/miss visual badges.
  - Collapsible league grouping with fixture counts.

### Phase 4: Intelligence Views (`strongest.html`, `ai_picks.html`, `status.html`, `guide.html`)
- `strongest.html`: High-conviction tactical cards with scoreboards, hit rates, and model vs book agreement indicators.
- `ai_picks.html`: AI Intelligence Command page with screening percentage badge (`Est. XX%`), LLM commentary card, and model agreement tags.
- `status.html`: Operations Control Room with pipeline telemetry cards, audit rate metrics, and system health status.
- `guide.html`: Technical handbook layout with high-contrast glossary tables and methodology walk-throughs.

### Phase 5: Client-Side Interactivity (`dg/web/static/board.js`)
- Instant search filter by team or league.
- Quick market toggle filtering without full-page reloads.
- Collapsible league sections with memory.
- Smooth scroll to top and score confirmation micro-dialogs.

### Phase 6: Quality Control & Audit
- Run `pytest tests/test_web.py` to ensure 100% test passing.
- Run `python3 .agent/scripts/checklist.py .` to ensure 0 lint errors and passing UX/SEO audits.
