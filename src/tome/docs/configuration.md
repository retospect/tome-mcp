---
description: "config.yaml fields: roots, tex_globs, track, needful"
---
# Configuration

Tome reads `tome/config.yaml` from the project root. It is git-tracked
and SHA256-checksummed for cache invalidation.

## Sections

### `roots`

Named document entry points. Each maps a name to a `.tex` file:

```yaml
roots:
  default: main.tex
  talk: slides/main.tex
```

Most tools accept a `root` parameter that references these names.

### `tex_globs`

Glob patterns for corpus indexing (ChromaDB):

```yaml
tex_globs:
  - "sections/*.tex"
  - "appendix/*.tex"
```

### `track`

Project-specific LaTeX macro patterns to index. Each pattern has a
name, regex, and named groups:

```yaml
track:
  - name: review_finding
    pattern: '\\mrev\{(?P<id>[^}]+)\}\{(?P<severity>[^}]+)\}\{(?P<text>[^}]+)\}'
    groups: [id, severity, text]
```

Built-in patterns (label, ref, cite, section) are always indexed
without configuration.

### `needful`

Task definitions for the needful system. See `guide("needful")`.

```yaml
needful:
  tasks:
    - name: review_pass_a
      globs: ["sections/*.tex"]
      cadence: 168h
    - name: reindex_corpus
      globs: ["sections/*.tex", "appendix/*.tex"]
      cadence: 0
```

## Editing

Edit `tome/config.yaml` directly — no special tool needed.
Tome detects changes via SHA256 checksum on next tool call.

`set_root()` auto-creates a default config if none exists.
