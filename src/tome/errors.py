"""Exception hierarchy for Tome.

Every error message includes: what happened, why, and what to do next.
This allows LLM clients to understand failures and take corrective action.
"""


class TomeError(Exception):
    """Base class for all Tome errors."""


class PaperNotFound(TomeError):
    """Paper key not in library."""

    def __init__(self, key: str):
        super().__init__(
            f"No paper with key '{key}' in tome/references.bib. "
            f"Use list_papers to see available keys, or set_paper to create one."
        )
        self.key = key


class PageOutOfRange(TomeError):
    """Requested page number exceeds paper's page count."""

    def __init__(self, key: str, page: int, total: int):
        super().__init__(
            f"Paper '{key}' has {total} pages. Requested page {page}. " f"Valid range: 1-{total}."
        )
        self.key = key
        self.page = page
        self.total = total


class DuplicateKey(TomeError):
    """Bib key already exists in the library."""

    def __init__(self, key: str):
        super().__init__(
            f"Bib key '{key}' already exists in the library. "
            f"Use set_paper key='{key}' to update it, "
            f"or choose a different key (e.g. '{key}a')."
        )
        self.key = key


class DOIResolutionFailed(TomeError):
    """CrossRef returned an error for a DOI lookup."""

    def __init__(self, doi: str, status_code: int):
        if status_code == 404:
            msg = (
                f"DOI '{doi}' does not exist (CrossRef 404). "
                f"This DOI may be hallucinated. "
                f"Use set_paper to remove it or provide a correct one."
            )
        elif status_code == 429:
            msg = (
                f"CrossRef rate-limited (429) while checking DOI '{doi}'. "
                f"Try again in a few seconds."
            )
        else:
            msg = (
                f"CrossRef returned HTTP {status_code} for DOI '{doi}'. "
                f"This may be a transient error. Try again later."
            )
        super().__init__(msg)
        self.doi = doi
        self.status_code = status_code


class IngestFailed(TomeError):
    """Could not identify or process a paper from a PDF."""

    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Could not ingest '{path}': {reason}. "
            f"The file remains in inbox/. "
            f"Try: ingest path='{path}' key='authorYYYY' to assign a key manually, "
            f"or use set_paper to create the bib entry first."
        )
        self.path = path
        self.reason = reason


class BibParseError(TomeError):
    """The bib file could not be parsed."""

    def __init__(self, path: str, detail: str):
        super().__init__(
            f"Failed to parse '{path}': {detail}. "
            f"Check the file for syntax errors (unmatched braces, missing commas). "
            f"The file was not modified."
        )
        self.path = path
        self.detail = detail


class BibWriteError(TomeError):
    """Roundtrip test failed — write aborted to protect data."""

    def __init__(self, path: str, detail: str):
        super().__init__(
            f"Bib write aborted for '{path}': {detail}. "
            f"A roundtrip parse-serialize-parse test detected unexpected changes. "
            f"The file was not modified. A backup exists at .tome/tome.json.bak."
        )
        self.path = path
        self.detail = detail


class FigureNotFound(TomeError):
    """Requested figure does not exist for this paper."""

    def __init__(self, key: str, figure: str):
        super().__init__(
            f"No figure '{figure}' registered for paper '{key}'. "
            f"Use request_figure to create a figure request, "
            f"or list_figures to see existing figures."
        )
        self.key = key
        self.figure = figure


class TextNotExtracted(TomeError):
    """Paper exists but text has not been extracted yet."""

    def __init__(self, key: str):
        super().__init__(
            f"Text not yet extracted for paper '{key}'. "
            f"Run rebuild key='{key}' to extract text from the PDF, "
            f"or check that the PDF exists in tome/pdf/."
        )
        self.key = key


class APIError(TomeError):
    """An external API returned an error after retries were exhausted."""

    def __init__(self, service: str, status_code: int, detail: str = ""):
        if status_code == 429:
            msg = (
                f"{service} rate-limited (HTTP 429) after retries. "
                f"Wait a minute and try again. {detail}"
            )
        elif status_code >= 500:
            msg = (
                f"{service} server error (HTTP {status_code}) after retries. "
                f"The service may be temporarily down. Try again later. {detail}"
            )
        elif status_code == 0:
            msg = (
                f"{service} unreachable (connection timeout after retries). "
                f"Check your network connection. {detail}"
            )
        else:
            msg = (
                f"{service} returned HTTP {status_code}. {detail}"
            )
        super().__init__(msg.strip())
        self.service = service
        self.status_code = status_code


class UnsafeInput(TomeError):
    """Input contains path traversal or other unsafe characters."""

    def __init__(self, field: str, value: str, reason: str):
        super().__init__(
            f"Rejected unsafe {field}='{value}': {reason}. "
            f"Keys must be alphanumeric with optional hyphens, underscores, and dots. "
            f"Paths must not contain '..' or be absolute."
        )
        self.field = field
        self.value = value
        self.reason = reason


class ConfigError(TomeError):
    """Project configuration is missing or invalid."""

    def __init__(self, detail: str, hint: str = ""):
        msg = f"Configuration error: {detail}."
        if hint:
            msg += f" {hint}"
        super().__init__(msg)
        self.detail = detail
        self.hint = hint


class ConfigMissing(ConfigError):
    """The tome/config.yaml file does not exist yet."""

    def __init__(self, tome_dir: str):
        super().__init__(
            f"No config.yaml found in {tome_dir}/",
            hint=(
                "Run set_root(path='...') to auto-create a starter config, "
                "or create tome/config.yaml manually. "
                "The config file defines document roots, tex_globs for search indexing, "
                "tracked LaTeX macros, and recurring tasks."
            ),
        )


class RootNotFound(ConfigError):
    """A named document root is not defined in config.yaml."""

    def __init__(self, root: str, available: list[str]):
        avail_str = ", ".join(f"'{r}'" for r in available) if available else "(none defined)"
        super().__init__(
            f"Document root '{root}' not found in config.yaml. Available roots: {avail_str}",
            hint=(
                "Add this root to the 'roots:' section of tome/config.yaml, e.g.:\n"
                f"  roots:\n    {root}: path/to/{root}.tex\n"
                "Or use an existing root name."
            ),
        )


class RootFileNotFound(ConfigError):
    """The .tex file for a document root does not exist on disk."""

    def __init__(self, root_name: str, tex_path: str, project_root: str):
        super().__init__(
            f"Root '{root_name}' points to '{tex_path}' but that file does not exist "
            f"under project root {project_root}",
            hint=(
                "Check that the path in tome/config.yaml is correct and relative "
                "to the project root. Create the file or update the config."
            ),
        )


class NoBibFile(ConfigError):
    """No references.bib exists yet."""

    def __init__(self, bib_path: str):
        super().__init__(
            f"Bibliography file not found at {bib_path}",
            hint=(
                "The library is empty. Use set_paper(key='...', title='...') to create "
                "the first entry, or place a PDF in tome/inbox/ and run ingest."
            ),
        )


class NoTexFiles(ConfigError):
    """tex_globs matched no files."""

    def __init__(self, globs: list[str]):
        globs_str = ", ".join(globs)
        super().__init__(
            f"No files matched tex_globs: [{globs_str}]",
            hint=(
                "Check that tex_globs in tome/config.yaml match your project structure. "
                "Common patterns: 'sections/*.tex', 'chapters/*.tex', '**/*.tex'. "
                "Directories .tome/, .git/, .venv/ are always excluded."
            ),
        )


class ChromaDBError(TomeError):
    """ChromaDB initialization or query failed."""

    def __init__(self, detail: str):
        super().__init__(
            f"ChromaDB error: {detail}. "
            f"The .tome/chroma/ directory may be corrupted. "
            f"Try: rebuild (re-extracts text and re-indexes all papers). "
            f"If that fails, delete .tome/chroma/ and rebuild again."
        )
        self.detail = detail


class UnpaywallNotConfigured(ConfigError):
    """No email configured for Unpaywall API access."""

    def __init__(self):
        super().__init__(
            "No email configured for Unpaywall open-access PDF lookup",
            hint=(
                "Set the UNPAYWALL_EMAIL environment variable, or add "
                "'unpaywall_email: you@example.com' to tome/config.yaml. "
                "Unpaywall requires an email for API access (they don't spam)."
            ),
        )
