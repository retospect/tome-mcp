# .tome Archive Format — v1

Self-contained ZIP archive for a single document. Contains everything
needed to rebuild catalog.db and ChromaDB without the original PDF.

## Layout

```
<key>.tome  (ZIP, DEFLATED)
├── manifest.json          # version + key + content_hash
├── meta.json              # full document metadata
├── pages/
│   ├── p01.txt            # raw extracted text, 1-indexed
│   ├── p02.txt
│   └── ...
├── chunks.json            # ordered chunks with page + char offsets
└── embeddings.npy         # float32 [N, 384] numpy array (all-MiniLM-L6-v2)
```

## manifest.json

Minimal top-level metadata for quick reads without parsing full meta.

```json
{
  "format_version": 1,
  "key": "tinti2017intrusion",
  "content_hash": "f408df064f2bc8c5...",
  "embedding_model": "all-MiniLM-L6-v2",
  "embedding_dim": 384,
  "created_at": "2026-02-16T15:33:00Z"
}
```

## meta.json

Full document metadata (same fields as catalog.db `documents` table).
Used by `catalog_rebuild()` to repopulate catalog.db.

Required fields: `key`, `content_hash`, `title` (non-empty).

## pages/p{NN}.txt

Raw extracted text per page, 1-indexed (`p01.txt` = page 1).
Preserved exactly as extracted by PyMuPDF.

## chunks.json

Ordered array of chunk objects. Each chunk maps to one row in
`embeddings.npy` (same index).

```json
[
  {
    "text": "chunk text...",
    "page": 1,
    "char_start": 0,
    "char_end": 487,
    "token_count": 128
  },
  ...
]
```

- **page**: 1-indexed source page
- **char_start/char_end**: character offsets within the page text
- **token_count**: token count from the chunker

## embeddings.npy

NumPy `.npy` format, shape `[N, 384]`, dtype `float32`.
Row `i` is the embedding for `chunks.json[i]`.

Model: `all-MiniLM-L6-v2` (ChromaDB default).

## Rebuild Contract

From a `.tome` archive alone, you can:

1. **Rebuild catalog.db** — read `meta.json`, call `catalog_upsert()`
2. **Rebuild ChromaDB** — read `chunks.json` + `embeddings.npy`,
   call `collection.upsert(ids, documents, embeddings, metadatas)`
   with pre-computed embeddings (no re-embedding needed)
3. **Serve page text** — read `pages/p{NN}.txt` directly
4. **Serve chunk context** — read `chunks.json` for paragraph-level citations

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
