# .tome Archive Format — v1

Self-contained HDF5 file for a single document. Contains everything
needed to rebuild catalog.db and ChromaDB without the original PDF.

## Layout

```
<key>.tome  (HDF5)
├── attrs:                 # root attributes — quick access
│   ├── format_version     # int: 1
│   ├── key                # str: "tinti2017intrusion"
│   ├── content_hash       # str: "f408df064f2bc8c5..."
│   ├── embedding_model    # str: "all-MiniLM-L6-v2"
│   ├── embedding_dim      # int: 384
│   └── created_at         # str: ISO-8601 timestamp
├── meta                   # dataset: JSON string (full DocumentMeta)
├── pages                  # dataset: vlen UTF-8 string array [P]
└── chunks/                # group (optional, present when embedded)
    ├── texts              # dataset: vlen UTF-8 string array [N]
    ├── embeddings         # dataset: float32 [N, 384]
    ├── pages              # dataset: int32 [N] (1-indexed page number)
    ├── char_starts        # dataset: int32 [N]
    └── char_ends          # dataset: int32 [N]
```

## Root Attributes

Quick-access scalars — readable without loading any datasets.

```python
import h5py
with h5py.File("tinti2017intrusion.tome", "r") as f:
    f.attrs["format_version"]    # → 1
    f.attrs["key"]               # → "tinti2017intrusion"
    f.attrs["content_hash"]      # → "f408df064f2bc8c5..."
```

## meta (dataset)

Full DocumentMeta as a JSON string (same fields as catalog.db `documents` table).
Used by `catalog_rebuild()` to repopulate catalog.db.

Required fields: `key`, `content_hash`, `title` (non-empty).

Stored as JSON because DocumentMeta has complex nested dicts (type_metadata,
pdf_metadata, xmp_metadata, title_sources) that don't map to HDF5 attrs.

## pages (dataset)

Variable-length UTF-8 string array. Index 0 = page 1.
Preserved exactly as extracted by PyMuPDF.

Random access: `f["pages"][3]` reads only page 4.

## chunks/ (group)

Present when the document has been chunked and embedded.

- **texts**: chunk text strings, index-aligned with embeddings
- **embeddings**: float32 `[N, 384]`, pre-computed all-MiniLM-L6-v2
- **pages**: 1-indexed source page per chunk
- **char_starts/char_ends**: character offsets within the page text

Random access: `f["chunks/embeddings"][42]` reads one vector without loading all.

## Rebuild Contract

From a `.tome` archive alone, you can:

1. **Rebuild catalog.db** — read `meta` dataset, call `catalog_upsert()`
2. **Rebuild ChromaDB** — read `chunks/texts` + `chunks/embeddings`,
   call `collection.upsert(ids, documents, embeddings, metadatas)`
   with pre-computed embeddings (no re-embedding needed)
3. **Serve page text** — read `pages[N]` directly (memory-mapped)
4. **Serve chunk context** — read `chunks/texts` for paragraph-level citations

## Vault Directory Layout

```
~/.tome-mcp/
├── pdf/
│   ├── a/
│   │   ├── ahmad2024mof.pdf
│   │   └── alaghi2013stochastic.pdf
│   └── t/
│       └── tinti2017intrusion.pdf
├── tome/
│   ├── a/
│   │   ├── ahmad2024mof.tome
│   │   └── alaghi2013stochastic.tome
│   └── t/
│       └── tinti2017intrusion.tome
├── catalog.db
└── chroma/
```

Files are sharded by first character of the key into subdirectories.
