# `get_quote` — Paragraph-level quote extraction from raw PDF text

## Problem

The primary consumer workflow is:

1. `search` (semantic) → find relevant chunks, get `key` + `page`
2. Need the **exact verbatim text** for `\mciteboxp{key}{page}{"quote..."}`
3. Current path: `grep_raw` (often fails on long phrases) → fallback to `get_page` (returns whole page, ~3000 chars of noise) → manually locate the paragraph

This takes 2–3 tool calls where 1 should suffice. `grep_raw` fails silently when:
- The query spans a hyphenated line break with a zero-width space (common in Nature/ACS PDFs)
- An OCR ligature or Unicode dash doesn't match after normalization
- The query is slightly paraphrased from the actual text (common when working from `search` chunk summaries)

## Proposed tool: `get_quote`

### Signature

```python
@mcp_server.tool()
def get_quote(
    key: str,
    query: str,
    page: int = 0,
    n: int = 1,
    context: str = "paragraph",   # "paragraph" | "sentence" | "chars"
    context_chars: int = 400,     # only used when context="chars"
) -> str:
```

### Args

| Arg | Description |
|-----|-------------|
| `key` | Bib key (required — single-paper search) |
| `query` | Natural language query OR partial phrase. Does NOT need to be exact. |
| `page` | Page number hint (0 = search all pages). When provided, searches only that page — much faster and more precise. |
| `n` | Number of best matches to return (default 1) |
| `context` | What to return around the match: `"paragraph"` (default), `"sentence"`, or `"chars"` |
| `context_chars` | Character window size when `context="chars"` |

### Return format

```json
{
  "key": "feng2022",
  "matches": [
    {
      "page": 6,
      "score": 0.92,
      "text": "The first systematic investigation of the dynamics of MIMs in MOFs was performed in 2012 by Loeb and colleagues in UWDM-1 (UWDM stands for University of Windsor Dynamic Material). The Canadian group chose to construct their robust dynamic system employing Cu2(COO)4 SBUs and [2]rotaxane linkers containing single [24]crown-6 macrocyclic polyethers as a prototype (Fig. 4c) to investigate internal dynamics in the extended solid-state structure.",
      "text_raw": "<the original text with line breaks and hyphens preserved, for copy-paste verification>"
    }
  ]
}
```

### Key design: `text` vs `text_raw`

- **`text`**: Cleaned paragraph — line-break hyphens rejoined, whitespace collapsed, zero-width spaces removed. Ready to paste into `\mciteboxp{}`.
- **`text_raw`**: Original extracted text with line breaks preserved. For verification against the PDF.

## Matching strategy

### Phase 1: Paragraph segmentation (preprocessing)

Split each page's raw text into paragraphs at **blank-line boundaries** (the natural break in PDF extractions). This is the key insight — raw PDF text reliably has `\n\n` between paragraphs but `\n` within paragraphs from column wrapping.

```python
def segment_paragraphs(raw_text: str) -> list[Paragraph]:
    """Split raw page text into paragraphs.
    
    A paragraph boundary is:
    - Two or more consecutive newlines (\n\n+)
    - A newline followed by a line that starts with a capital letter 
      after significant indentation change
    
    Filter out:
    - Paragraphs that are purely chemical formulae / figure labels
      (heuristic: >50% non-alpha characters)
    - Paragraphs shorter than 40 characters (captions, headers)
    """
```

Each `Paragraph` has:
- `text_raw: str` — original text with internal line breaks
- `text_clean: str` — rejoined, normalized (same pipeline as `grep_raw.normalize()` but preserving case)
- `text_norm: str` — fully normalized (lowered, collapsed) for matching
- `page: int`
- `char_offset: int` — position in original page text

### Phase 2: Matching (three tiers, cascading)

Given a query string, try these in order. Return the first tier that produces results.

#### Tier 1: Exact normalized substring match
Same as current `grep_raw` — normalize query and search within `text_norm`. Fast, precise.

#### Tier 2: Token-proximity match (NEW)
When exact match fails (the common case for long queries):

```python
def token_proximity_score(query_norm: str, paragraph_norm: str) -> float:
    """Score based on how many query tokens appear close together.
    
    1. Tokenize query into words (split on whitespace)
    2. For each query token, find its positions in the paragraph
    3. Find the minimum window in the paragraph that contains 
       the most query tokens
    4. Score = (tokens_found / total_query_tokens) * (1 / log(window_size + 1))
    
    This handles:
    - OCR artifacts splitting/mangling 1-2 words
    - Slightly reworded queries
    - Line-break artifacts that normalization missed
    """
```

#### Tier 3: Semantic similarity (NEW — optional, uses embeddings)
If tiers 1 and 2 fail (score below threshold), fall back to computing cosine similarity between the query embedding and each paragraph embedding. This handles fully paraphrased queries.

```python
# Only compute embeddings if tiers 1-2 failed
# Reuse the same embed_fn from ChromaDB
embed_fn = store.get_embed_fn()
query_emb = embed_fn([query])
para_embs = embed_fn([p.text_clean for p in paragraphs])
scores = cosine_similarity(query_emb, para_embs)
```

### Phase 3: Context expansion

If `context="paragraph"` (default): return the matched paragraph as-is.

If `context="sentence"`: return only the sentence(s) within the paragraph that contain the matched tokens. Use a simple sentence splitter (split on `. ` followed by uppercase letter).

If `context="chars"`: return a character window (current `grep_raw` behavior).

## Implementation plan

### File: `tome/src/tome/grep_raw.py`

Add to existing file (it already has the `normalize()` function):

```python
@dataclass
class Paragraph:
    text_raw: str
    text_clean: str  
    text_norm: str
    page: int
    char_offset: int

@dataclass  
class QuoteMatch:
    key: str
    page: int
    score: float
    text: str       # cleaned, ready for \mciteboxp
    text_raw: str   # original with line breaks

def segment_paragraphs(raw_text: str, page: int) -> list[Paragraph]:
    ...

def clean_for_quote(raw_para: str) -> str:
    """Clean a raw paragraph for use in \\mciteboxp.
    
    - Rejoin hyphenated line breaks
    - Remove zero-width spaces  
    - Collapse internal whitespace to single spaces
    - Preserve case and punctuation
    - Strip figure/table labels at boundaries
    """
    ...

def score_proximity(query_tokens: list[str], text_norm: str) -> float:
    ...

def get_quote_from_paper(
    query: str,
    raw_dir: Path,
    key: str,
    page: int = 0,
    n: int = 1,
) -> list[QuoteMatch]:
    """Find the best-matching paragraph(s) for a query."""
    ...
```

### File: `tome/src/tome/server.py`

New MCP tool:

```python
@mcp_server.tool()
def get_quote(
    key: str,
    query: str, 
    page: int = 0,
    n: int = 1,
    context: str = "paragraph",
    context_chars: int = 400,
) -> str:
    """Extract verbatim quotes from paper PDFs for deep citations.

    Finds the paragraph(s) best matching your query in a paper's
    raw text. Returns cleaned text ready for \\mciteboxp{}.

    Three matching tiers (cascading): exact normalized substring,
    token-proximity (handles OCR/linebreak artifacts), and
    semantic similarity (handles paraphrased queries).

    Args:
        key: Bib key (required).
        query: Text to find — exact phrase, partial phrase, or description.
        page: Page hint (0 = all pages). Providing page is faster and more precise.
        n: Number of best matches to return.
        context: Unit of text to return: 'paragraph', 'sentence', or 'chars'.
        context_chars: Window size when context='chars'.
    """
```

### Backward compatibility

- **Keep `grep_raw` as-is** — it's still useful for cross-paper search and exact verification
- `get_quote` is the new primary tool for the "find me a quotable passage" workflow
- `get_quote` requires `key` (single-paper only) — this is intentional; cross-paper quote extraction isn't a real use case

## Example workflows

### Current (3 tool calls)
```
1. search(query="molecular crosstalk in MOF", key="saurasanmartin2022")
   → chunk on page 24, partial text
2. grep_raw(query="pioneering example of cooperatively...", key="saurasanmartin2022")  
   → 0 results (line break in middle of phrase)
3. get_page(key="saurasanmartin2022", page=24)
   → 3000 chars, manually find the paragraph
```

### New (1 tool call)
```
1. get_quote(key="saurasanmartin2022", query="molecular crosstalk cooperatively functioning", page=24)
   → Returns the exact paragraph, cleaned, ready for \mciteboxp
```

### Fallback from search
```
1. search(query="conductance switching bistable MOF", key="feng2022")
   → chunk_113, page 6, mentions "electrochemically switchable states"
2. get_quote(key="feng2022", query="electrochemically switchable states within a robust framework", page=6)
   → Returns the full paragraph with that sentence
```

## Edge cases to handle

1. **Chemical formulae paragraphs**: Pages often have structural formulae rendered as text (COOH, O, N on separate lines). The paragraph segmenter should filter these out (heuristic: >50% non-alpha or <40 chars).

2. **Figure captions**: Often adjacent to body text. Include them in results but rank them lower (they tend to be shorter and have "Fig." prefix).

3. **Multi-column layouts**: PDF extraction sometimes interleaves columns. Paragraph segmentation on `\n\n` handles this because extractors typically preserve paragraph breaks even across columns.

4. **References section**: Pages with bibliography entries. These should be deprioritized (heuristic: many entries matching `\d+\.\s+[A-Z]` pattern).

5. **Zero results**: If all three tiers fail to produce a match above threshold, return an empty result with a suggestion: `"hint": "Try get_page(key=X, page=Y) for the full page text"`.

## Performance notes

- Tier 1 (exact): O(n) string search per page — fast
- Tier 2 (proximity): O(n*m) where n=query tokens, m=paragraph tokens — fast for single pages
- Tier 3 (semantic): Requires embedding computation — ~100ms per page. Only triggered as fallback.
- With `page` hint: searches 1 page instead of all — should be <50ms for tiers 1-2

## Testing

Key test cases:
1. Exact match (same as grep_raw)
2. Match with line-break hyphen in the middle of query
3. Match with zero-width space in source text
4. Match with 1-2 OCR errors in source text  
5. Paraphrased query (tier 3 only)
6. Chemical formula page filtering
7. Multiple matches on same page (n>1)
8. Page hint vs full-paper search consistency
