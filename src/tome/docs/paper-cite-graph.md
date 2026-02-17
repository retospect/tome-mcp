---
description: "Citation graph exploration: cited_by, cites"
---
# Citation Graph

Explore the citation graph using the `paper` tool's search bag.

## Who cites this paper (forward in time)

```
paper(search=['cited_by:xu2022'])
```

Returns citing papers with metadata. Response includes a `reverse` hint
to see references. Use this to find follow-up work.

## What this paper cites (backward in time)

```
paper(search=['cites:xu2022'])
```

Returns referenced papers. Use this to trace foundational work.

## Workflow

1. Start from a seed paper: `paper(id='xu2022')` to get metadata.
2. Explore forward: `paper(search=['cited_by:xu2022'])`.
3. Explore backward: `paper(search=['cites:xu2022'])`.
4. For any interesting result, get metadata: `paper(id='result_key')`.
5. Take notes: `notes(on='result_key', title='Relevance', content='...')`.
6. Find online: `paper(search=['topic', 'online'])` for federated search.

## Tips

- Follow the **hints** in every response — they suggest reverse direction,
  pagination, and next actions.
- Combine with `notes` to build institutional memory as you explore.
- Use `paper(id='key:page1')` to quickly verify a result before citing.
