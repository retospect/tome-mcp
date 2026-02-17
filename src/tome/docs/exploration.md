---
description: "Citation graph exploration with paper(search=[...])"
---
# Citation Exploration

Explore the citation graph using the `paper` tool's search bag.

## Workflow

1. **Who cites this paper**: `paper(search=['cited_by:sheberla2014'])`
   Returns citing papers with metadata.

2. **What this paper cites**: `paper(search=['cites:sheberla2014'])`
   Returns referenced papers.

3. **Online discovery**: `paper(search=['MOF conductivity', 'online'])`
   Federated search across S2 + OpenAlex.

4. **Read a result**: `paper(id='key')` for metadata, then
   `paper(id='key:page1')` for content.

5. **Take notes**: `notes(on='key', title='Relevance', content='...')`

## Tips

- Follow the **hints** in every response — they suggest the next
  logical action.
- Use `paper(search=['cited_by:key'])` to go forward in time
  (who builds on this work).
- Use `paper(search=['cites:key'])` to go backward (what informed
  this work).
- Combine with `notes` to build institutional memory as you explore.
