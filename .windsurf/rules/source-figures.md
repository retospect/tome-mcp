# Source Figures

- Source paper figures → `tome/figures/<bibkey>/`, never `figures/`
- `figures/` is for project-generated content only
- Always paired: `<bibkey>_<figid>_<slug>.png` + `.yaml` in same folder
- Caption from Tome `paper(key, page=page)`; ask user if extraction fails
- Tight crop default → always use `width=` in LaTeX
- Caption: context-appropriate text (not verbatim); end with "Reproduced from `\cite{key}`, Figure~X.Y."
- Label: `fig:<bibkey>-<short-slug>`; chemistry in proper LaTeX notation
- Request/track figures via Tome `figure` tool (request, register, or list)
