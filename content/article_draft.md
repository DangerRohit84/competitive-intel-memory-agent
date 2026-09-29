# I Stopped Losing Deals to Competitors I Barely Knew

I lost a $54K deal and I never saw it coming.

BluePeak Retail. 150 seats. We walked in at $68K talking platform and scale. Acme walked in at $54K with free migration and a 20% discount I never knew they offered. The champion signed before I heard the number. The loss note was two lines with no pricing detail and no lesson. A month later it happened again. My problem was not pitching. It was remembering.

Every rep held fragments. Priya had a discovery note with a real Acme quote. An SE had a screenshot proving Acme closed the SSO gap. A champion at Globex told us why we won — 365-day audit retention plus Mumbai hosting. But those fragments lived in call notes and hallway memory. By RFP time the context was gone and my battlecard was generic. I built the Competitive Intel Memory Agent to fix that.

I wanted something simple: retain everything once, recall the right three to five notes when the next RFP lands, and generate a battlecard that cites its evidence. I chose [Hindsight](https://github.com/vectorize-io/hindsight) as the memory layer for its retain and recall API with context, stable IDs, and tags. This primer on [what agent memory actually is](https://vectorize.io/what-is-agent-memory) shaped my design, and the reference lives in the [Hindsight docs](https://hindsight.vectorize.io/).

![Architecture Diagram - Streamlit UI calling Hindsight retain/recall, Groq composing grounded battlecard - PLACEHOLDER: export from architecture_mermaid.md via Figma at 1600x900 PNG](architecture_placeholder.png)

## Retain: 18 notes it would never forget

I started with 18 intel items across three rivals — Acme, Nova, Vertex — split across pricing, feature, win, and loss. Six per competitor. Notes I actually write: Acme quoted $48,000 per year for 200 seats to Northwind, 12% under us, onboarding free. Acme launched SSO plus SCIM in v4.2 but keeps logs only 30 days versus our 365. Won at Globex for $96K on residency. Lost at BluePeak on price. Nova and Vertex have their own tells — Nova flat-rate pricing and English-only summarizer, Vertex premium pricing and slow webhooks.

Each note carries context, a stable document ID, and a competitor tag. Context like `competitive intel pricing` matters because the same sentence means different things in different settings. IDs like `intel-001` through `intel-018` give upsert semantics — re-seeding replaces instead of duplicating. Tags like `competitor:Acme` filter recall to one rival.

```python
# Snippet 1: Retain one intel note with context, stable ID, and tags
memory.retain(
    text="Acme quoted $48,000/yr for 200 seats to Northwind, 12% under us.",
    context="competitive intel pricing",
    document_id="intel-001",
    tags=["competitor:Acme", "pricing", "mid-market"],
    timestamp="2026-08-04T10:30:00Z",
)
```

```python
# Snippet 2: Seed all 18 notes idempotently (re-runs replace, never duplicate)
for item in seed_items:  # 18 items from seed.json
    memory.retain(
        text=item["content"],
        context=f"competitive intel {item['type']}",
        document_id=item["id"],  # intel-001 ... intel-018
        tags=[f"competitor:{item['competitor']}"] + item["tags"],
        timestamp=item["timestamp"],  # backdated for temporal recall
    )
```

I backdate every timestamp to the deal date. That unlocked temporal queries: what changed since March. Without it everything anchors to now. With it, asking what Acme changed since March 2026 returns the August SSO launch. My Streamlit left pane uses the same path — Retain New Intel stores text, context, ID, and tag through the identical function.

## Recall: three queries, one filtered memory

At RFP time I run three recalls, all filtered to the deal competitor, then fuse results. It is the highest-leverage ten lines in the project.

Recall one is factual: Acme pricing features. Recall two is temporal: what changed for Acme since March 2026. Recall three is battle-oriented: Acme win loss objections strengths weaknesses. All use `competitor:Acme` with strict matching so Nova notes never bleed in. Each uses mid budget, 2000 max tokens, and all three memory types.

```python
# Snippet 3: 3-way recall, competitor-filtered, then grounded generation
factual = memory.recall("Acme pricing features", tags=["competitor:Acme"], budget="mid")
temporal = memory.recall("What changed for Acme since March 2026?", tags=["competitor:Acme"], budget="mid")
battle = memory.recall("Acme win loss objections strengths weaknesses", tags=["competitor:Acme"], budget="mid")
memories = dedupe(factual + temporal + battle)[:5]
battlecard = groq.compose(rfp_text, memories)  # cites [Memory 1], [Memory 2]
```

One query collapses intent. Factual gets the $48K number and the $22 tier. Temporal gets the SSO launch and the 30-day limit. Battle gets the Globex win and the BluePeak loss. Three narrow queries with a strict filter beat one clever query every time I tested it. The UI shows its work as text, type, and score rows with Memory 1 and Memory 2 citations. Win rate is counted in code — never by the model. With no memory, the agent says so honestly.

![Screenshot 1 - Cold vs With-Memory side-by-side battlecards for Acme 400-seat RFP - PLACEHOLDER: capture Streamlit right pane at 1600x900 PNG](screenshot_cold_vs_memory_placeholder.png)

## Cold vs with-memory: the $48K proof

Same RFP, two outputs. 400-seat logistics deal needing SSO, audit logs, India hosting, per-seat pricing, versus Acme.

Cold, with no memory, it writes what every generic model writes: secure, scalable platform, competitive pricing, trusted everywhere. True and useless. No numbers, no traps, no proof.

With memory it is a different document. Pricing leads with the $48K Northwind quote and the $22 tier with the 500-seat break to $18. The trap tells the rep to ask about audit retention — Acme keeps 30 days, I keep 365. Proof cites the Globex $96K win on Mumbai hosting with CTO Maria D., plus CareWell $120K on HIPAA and 99.99% SLA. Every claim carries a citation.

That $48K number never appears cold. It always appears recalled, with source. When I show the side-by-side, nobody asks if memory matters. They ask how fast they can add notes.

![Screenshot 2 - Citations Memory 1 and Memory 2 plus recall trace rows - PLACEHOLDER: capture citations and trace panel at 1600x900 PNG](screenshot_citations_trace_placeholder.png)

## Lessons: consolidation lag and stable IDs

Lesson one stung. In rehearsal I retained two notes live, hit generate, and one was missing. The write had not failed. Retain is end-of-turn, recall is next-turn — observations consolidate asynchronously, so instant recall can miss the observation type. My fix: recall with retry polling three times, and seed before the demo, not during it. Fresh intel is visible within seconds, not instantly.

Lesson two saved me. Stable IDs sound boring until re-seeding creates eighteen duplicates. With intel-001 through intel-018, re-running replaces instead of appending. I reset the room with one command and show the identical trace. Context on every write plus strict tags on every read keeps behavior identical on run one and run fifty.

My stack stayed simple: Streamlit for the UI, Hindsight for retain and recall, Groq for grounded composition, seed.json as offline fallback so the demo never breaks without keys. Memory did not make my agent smarter. It made it specific. Specific wins deals.
