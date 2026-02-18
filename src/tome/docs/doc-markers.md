---
description: "Review markers: %TODO, fixme, mrev, review patterns"
---
# Document Markers & Review Workflow

## Finding markers

```
toc(search=['%TODO'])                       # find TODO comments
toc(search=['\fixme'])                      # find \fixme commands
toc(search=['%TODO', '\fixme'])             # find both at once
toc(search=['\mrev'])                       # find review findings
toc(search=['RIG-CON-001'])                 # locate specific finding
```

## Review → Commit cycle

1. **Review** the file (read, check claims, verify citations).
2. **Edit** — fix issues or add `\mrev{id}{severity}{text}` markers.
3. **Commit** your changes to git.
4. **Record**: `notes(on='sections/file.tex', title='Review', content='...')`

## Finding lifecycle (\mrev)

If your project tracks review findings via a `\mrev{}` macro:

1. **Create**: Add `\mrev{RIG-CON-001}{major}{Claim unsupported}`.
2. **Find all**: `toc(search=['\mrev'])`.
3. **Find one**: `toc(search=['RIG-CON-001'])`.
4. **Resolve**: Fix the issue, delete the `\mrev{}` marker.
5. **Audit**: `toc(search=['\mrev'])` — confirm count decreased.

## Configuration

Define tracked patterns in `tome/config.yaml` under `track:`:

```yaml
track:
  - name: todo
    pattern: '%\s*TODO'
  - name: fixme
    pattern: '\\fixme'
  - name: review_finding
    pattern: '\\mrev\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}'
    groups: [id, severity, text]
```

## Deep citations

Deep citations embed a verbatim quote from a source paper in your
LaTeX. Tome ships `examples/tome-deepcite.sty` with five macros:

| Macro | Arguments | Output |
|-------|-----------|--------|
| `\mciteboxp{key}{page}{quote}` | key, page, quote | Shaded block quote with page |
| `\mcitebox{key}{quote}` | key, quote | Shaded block quote (no page) |
| `\citeqp{key}{page}{quote}` | key, page, quote | Inline quote with page |
| `\citeq{key}{quote}` | key, quote | Inline quote |
| `\citeqm{key}{quote}` | key, quote | Inline quote, source in margin |

```latex
\usepackage{tome-deepcite}              % footnotes on (default)
\usepackage[nofootnotes]{tome-deepcite}  % footnotes off
```

### Writing deep cites

1. `paper(id='key:page3')` — read page text.
2. Copy the relevant quote into `\mciteboxp{key}{3}{...}`.

### Validating deep cites

Validation is just searching the paper for the quote:

1. `toc(search=['\mciteboxp'])` — find all deep cites in your .tex.
2. For each hit, `paper(id='key:pageN')` — read the cited page.
3. Compare the quote against the page text.

### Config: track deep cites as markers

```yaml
track:
  - name: deep_cite
    pattern: '\\mciteboxp\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}'
    groups: [key, page, quote]
```
