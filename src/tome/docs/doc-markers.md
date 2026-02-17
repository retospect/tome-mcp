---
description: "Review markers: %TODO, fixme, mrev, review patterns"
---
# Document Markers & Review Workflow

## Finding markers

```
doc(search=['%TODO'])                       # find TODO comments
doc(search=['\fixme'])                      # find \fixme commands
doc(search=['%TODO', '\fixme'])             # find both at once
doc(search=['\mrev'])                       # find review findings
doc(search=['RIG-CON-001'])                 # locate specific finding
```

## Review → Commit cycle

1. **Review** the file (read, check claims, verify citations).
2. **Edit** — fix issues or add `\mrev{id}{severity}{text}` markers.
3. **Commit** your changes to git.
4. **Record**: `notes(on='sections/file.tex', title='Review', content='...')`

## Finding lifecycle (\mrev)

If your project tracks review findings via a `\mrev{}` macro:

1. **Create**: Add `\mrev{RIG-CON-001}{major}{Claim unsupported}`.
2. **Find all**: `doc(search=['\mrev'])`.
3. **Find one**: `doc(search=['RIG-CON-001'])`.
4. **Resolve**: Fix the issue, delete the `\mrev{}` marker.
5. **Audit**: `doc(search=['\mrev'])` — confirm count decreased.

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

Deep citations embed verbatim quotes from source papers in LaTeX.
Configure validation:

```yaml
track:
  - name: deep_cite
    pattern: '\\mciteboxp\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}'
    groups: [key, page, quote]
```

Validate by reading page text with `paper(id='key:pageN')` and
comparing against quoted text. See `guide('document-analysis')`
for the full deep citation macro reference.
