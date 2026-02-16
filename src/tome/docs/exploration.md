---
description: "Citation beam search — explore, triage, expand"
---
# Citation Exploration

LLM-guided iterative exploration of the citation graph using
Semantic Scholar. Think of it as beam search over citations.

## Workflow

1. **Seed**: `explore(key="sheberla2014")` — fetches
   citing papers with abstracts. Each call = 2 S2 API requests.
   Results are cached (7-day TTL).

2. **Triage**: Present results as a table. For each paper, decide:
   - `relevant` — worth expanding further
   - `irrelevant` — dead end, prune this branch
   - `deferred` — possibly relevant, revisit later

3. **Mark**: `explore(s2_id=..., relevance="relevant", note="rationale")`
   for each paper. Batch the calls.

4. **Expand**: Call `explore(s2_id=<relevant_id>,
   parent_s2_id=<parent>, depth=<n+1>)` on relevant papers
   to go deeper.

5. **Repeat** until you've found what you need or branches
   are exhausted.

## Session continuity

`explore()` (no args) shows the full exploration state:
- What you've explored
- What's marked relevant (expand next)
- What's deferred (revisit later)
- Use `explore(action="expandable")` to see only relevant nodes not yet expanded

## Tips

- Be **narrow** (few relevant) for pointed searches.
- Be **broader** for survey-style exploration.
- `explore(action="clear")` resets session state without affecting
  the main citation tree or dismissed candidates.

## Related discover() scopes

- **`discover(scope="refresh", key="...")`** — Cache citation graphs from S2.
  With key: one paper. Without: batch refresh stale papers (30+ day).
- **`discover(scope="shared_citers", min_shared=2)`** — Find non-library papers
  citing ≥N of your references. Merges cite_tree + S2AG local data.
- **`explore(s2_id="...", action="dismiss")`** — Permanently hide a candidate.
