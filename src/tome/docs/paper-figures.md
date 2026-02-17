---
description: "Figure management: register, caption, delete"
---
# paper(id='key:figN') — Figures

Figures are screenshots from papers stored in the vault manifest.

## Register a figure

Take a screenshot and register it:

```
paper(id='xu2022:fig3', path='screenshots/fig3.png')
```

Response: `status: figure_ingested` with hints for caption and delete.

## Set a caption

```
paper(id='xu2022:fig3', meta='{"caption": "Band structure showing QI"}')
```

## View figure info

```
paper(id='xu2022:fig3')
```

Returns path, caption, and status.

## Delete a figure

```
paper(id='xu2022:fig3', delete=true)
```

## Listing figures

`paper(id='xu2022')` includes `has_figures` — a list of registered
figure names. If figures exist, the response hints include a direct
link to the first figure.

## Naming convention

Use `fig` + the figure number from the paper: `fig1`, `fig2`, `figS1`
(supplementary), `figS2`, etc. Arbitrary names like `fig_overview`
are also valid.
