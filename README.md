# Competitive Intel Memory Agent — HackwithHyderabad 3.0

## What it does

Sales teams lose deals because competitive knowledge lives in scattered call
notes, Slack threads, and lost RFP docs. This agent gives every rep a memory.

- **Retain** every pricing quote, feature gap, win and loss note to Hindsight.
- **Recall** the right 3–5 notes at RFP time, filtered by competitor.
- **Generate** a battlecard grounded in recalled evidence with citations,
  plus a cold-vs-memory comparison that shows the memory impact.

Built for HackwithHyderabad 3.0. Competitors tracked: **Acme, Nova, Vertex**.
Seed pack: 18 realistic intel items across pricing / feature / win / loss.

## Architecture (text diagram)

```
+----------------+      retain(text, context, doc_id, tags)     +----------------+
| streamlit_app  |  ------------------------------------------> |   HINDSIGHT    |
|  (UI, left     |                                              |   memory API   |
|   pane input)  |  <------------------------------------------ |                |
+-------+--------+   recall(query, competitor filter, top_k)    +-------+--------+
        |                                                              |
        |  generate_battlecard(rfp_text, competitor)                    |
        v                                                              |
+---------------+   1. recall memories   +------------------+          |
|  src/agent.py |  --------------------> | hindsight_client | ---------+
|  (battlecard  |  <-------------------- | retain / recall  |
|   composer)   |   2. compose markdown  +------------------+
+-------+-------+
        |
        |  {battlecard_md, citations, memory_impact}
        v
+----------------+
| streamlit_app  |
| (right pane:   |
|  Cold vs With  |
|  memory, cites,|
|  trace, wins)  |
+----------------+
Offline path: no keys -> recall + compose directly from seed.json.
```

## Hindsight retain / recall points

**Retain (write path):**
- `seed.py` — `retain(text=content, context="competitive intel <type>", document_id=<stable id>, tags=["competitor:<X>", ...])` for each of the 18 seed items.
- `streamlit_app.py` left pane — **Retain new intel** button calls the same
  `retain()` with the selected competitor + intel type.
- Document IDs are stable (`intel-001` … `intel-018`) so re-seeding is idempotent.

**Recall (read path):**
- `src/agent.py:generate_battlecard(rfp_text, competitor)` recalls top notes
  filtered by `competitor:<X>` and composes the battlecard.
- `streamlit_app.py` shows the recall trace as `text | type | score` rows,
  top 2 citations as Memory 1 / Memory 2, and a `memory_impact` line.
- Without keys the UI falls back to keyword recall over `seed.json`
  so the demo never breaks.

## Setup

```bash
# 1. env
cp .env.example .env
# edit .env -> HINDSIGHT_API_KEY, HINDSIGHT_BANK_ID, HINDSIGHT_BASE_URL, etc.

# 2. install
pip install -r requirements.txt

# 3. seed memory (dry run first)
python seed.py --dry-run
python seed.py

# 4. run UI
streamlit run streamlit_app.py
```

Open http://localhost:8501 — left pane for input, right pane for
Cold vs With-memory battlecards, citations, recall trace, and win-rate dashboard.

## Live demo / video / write-up

- Live demo: _TODO — add deployed Streamlit URL here_
- YouTube walkthrough (120s): _TODO — add video link here_
- Article / deep-dive: _TODO — add blog link here_

## Links

- Hindsight repo: https://github.com/vectorize-io/hindsight
- Hindsight docs: https://hindsight.vectorize.io/
- What is agent memory: https://vectorize.io/what-is-agent-memory
- Hindsight dashboard: https://ui.hindsight.vectorize.io

## Files (junior-dev scope)

- `seed.json` — 18 intel items (Acme / Nova / Vertex x pricing / feature / win / loss)
- `seed.py` — loader + retain + `--dry-run`
- `streamlit_app.py` — 2-pane UI with offline fallback + win-rate dashboard
- `README.md` — this file
- `demo_script.md` — 120-second demo script
