"""Exception hierarchy for Tome.

Every error message includes: what happened, why, and what to do next.
This allows LLM clients to understand failures and take corrective action.
"""


class TomeError(Exception):
    """Base class for all Tome errors."""


class PaperNotFound(TomeError):
    """Paper key not in library."""

    def __init__(self, key: str, near: list[str] | None = None):
        msg = f"No paper with key '{key}' in tome/references.bib."
        if near:
            suggestions = ", ".join(f"'{k}'" for k in near[:5])
            msg += f" Similar keys: {suggestions}."
        msg += (
            " Use paper(action='list') to browse keys,"
            " search(query='...') to find by topic,"
            " or paper(key='...', title='...') to create one."
        )
        super().__init__(msg)
        self.key = key
        self.near = near or []


class PageOutOfRange(TomeError):
    """Requested page number exceeds paper's page count."""

    def __init__(self, key: str, page: int, total: int):
        msg = f"Paper '{key}' has {total} pages. Requested page {page}. Valid range: 1-{total}."
        if page <= 0:
            msg += " Note: pages are 1-indexed. Use page=1 for the first page."
        super().__init__(msg)
        self.key = key
        self.page = page
        self.total = total


class DuplicateKey(TomeError):
    """Bib key already exists in the library or vault."""

    def __init__(self, key: str):
        super().__init__(
            f"Key '{key}' already exists. "
            f"Use paper(key='{key}', title='...') to update it, "
            f"paper(action='rename', key='{key}', new_key='...') to rename it, "
            f"or choose a different key (e.g. '{key}a')."
        )
        self.key = key


class DuplicateDOI(TomeError):
    """A document with this DOI already exists in the vault."""

    def __init__(self, doi: str, existing_key: str = ""):
        extra = f" (existing key: '{existing_key}')" if existing_key else ""
        super().__init__(
            f"DOI '{doi}' already exists in the vault{extra}. "
            f"This PDF may be a duplicate. "
            f"Use paper(key='...') to inspect the existing entry, "
            f"or ingest with a different key if this is a distinct document."
        )
        self.doi = doi
        self.existing_key = existing_key


class DuplicateExternalID(TomeError):
    """A document with this external ID already exists in the vault."""

    def __init__(self, external_id: str, existing_key: str = ""):
        extra = f" (existing key: '{existing_key}')" if existing_key else ""
        super().__init__(
            f"External ID '{external_id}' already exists in the vault{extra}. "
            f"This may be a duplicate patent, datasheet, or standard."
        )
        self.external_id = external_id
        self.existing_key = existing_key


class DOIResolutionFailed(TomeError):
    """CrossRef returned an error for a DOI lookup."""

    def __init__(self, doi: str, status_code: int):
        if status_code == 404:
            msg = (
                f"DOI '{doi}' does not exist (CrossRef 404). "
                f"This DOI may be hallucinated (~10%% of AI-sourced DOIs are wrong). "
                f"Use discover(query='<paper title>') to find the real DOI, "
                f"paper(key='...', doi='<correct>') to fix it, "
                f"or doi(action='reject', doi='{doi}') to reject it."
            )
        elif status_code == 429:
            msg = (
                f"CrossRef rate-limited (429) while checking DOI '{doi}'. "
                f"Try again in a few seconds."
            )
        else:
            msg = (
                f"CrossRef returned HTTP {status_code} for DOI '{doi}'. "
                f"This may be a transient error. Try again later. "
                f"If this persists, use report_issue to log it — see guide('reporting-issues')."
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
            f"Try: ingest(path='{path}', key='authorYYYYslug') to assign a key manually, "
            f"or use paper(key='authorYYYYslug', title='...') to create the bib entry first. "
            f"See guide('paper-workflow') for the full pipeline. "
            f"If the PDF looks valid, use report_issue to log a bug — see guide('reporting-issues')."
        )
        self.path = path
        self.reason = reason


class BibParseError(TomeError):
    """The bib file could not be parsed."""

    def __init__(self, path: str, detail: str):
        super().__init__(
            f"Failed to parse '{path}': {detail}. "
            f"Check the file for syntax errors (unmatched braces, missing commas). "
            f"The file was not modified. "
            f"Try: git diff {path} to see recent changes. "
            f"If the file looks correct, use report_issue to log a bug — see guide('reporting-issues')."
        )
        self.path = path
        self.detail = detail


class BibWriteError(TomeError):
    """Roundtrip test failed — write aborted to protect data."""

    def __init__(self, path: str, detail: str):
        super().__init__(
            f"Bib write aborted for '{path}': {detail}. "
            f"A roundtrip parse-serialize-parse test detected unexpected changes. "
            f"The file was not modified. A backup exists at .tome-mcp/tome.json.bak. "
            f"If this recurs, use report_issue to log a bug — see guide('reporting-issues')."
        )
        self.path = path
        self.detail = detail


class FigureNotFound(TomeError):
    """Requested figure does not exist for this paper."""

    def __init__(self, key: str, figure: str):
        super().__init__(
            f"No figure '{figure}' registered for paper '{key}'. "
            f"Use figure(key='{key}', figure='...', reason='...') to request it, "
            f"or figure() to list existing figures."
        )
        self.key = key
        self.figure = figure


class TextNotExtracted(TomeError):
    """Paper exists but text has not been extracted yet."""

    def __init__(self, key: str, has_pdf: bool | None = None):
        if has_pdf is False:
            msg = (
                f"No PDF for paper '{key}'. "
                f"Place the PDF in tome/inbox/ and run ingest, "
                f"or try doi(key='{key}', action='fetch') for open-access retrieval."
            )
        elif has_pdf is True:
            msg = (
                f"PDF exists for '{key}' but text not yet extracted. "
                f"Run reindex(key='{key}') to extract and index it."
            )
        else:
            msg = (
                f"Text not yet extracted for paper '{key}'. "
                f"Run reindex(key='{key}') to extract text from the PDF, "
                f"or check that the PDF exists in tome/pdf/."
            )
        super().__init__(msg)
        self.key = key
        self.has_pdf = has_pdf


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
                f"The service may be temporarily down. Try again later. "
                f"If this persists, use report_issue to log it — see guide('reporting-issues'). {detail}"
            )
        elif status_code == 0:
            msg = (
                f"{service} unreachable (connection timeout after retries). "
                f"Check your network connection. {detail}"
            )
        else:
            msg = f"{service} returned HTTP {status_code}. {detail}"
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


class RootNotFound(ConfigError):
    """A named document root is not defined in config.yaml."""

    def __init__(self, root: str, available: list[str]):
        avail_str = ", ".join(f"'{r}'" for r in available) if available else "(none defined)"
        super().__init__(
            f"Document root '{root}' not found in config.yaml. Available roots: {avail_str}",
            hint=(
                "Add this root to the 'roots:' section of tome/config.yaml, e.g.:\n"
                f"  roots:\n    {root}: path/to/{root}.tex\n"
                "Or use an existing root name. "
                "See guide('configuration') for details."
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
                "to the project root. Create the file or update the config. "
                "See guide('configuration') for details."
            ),
        )


class NoBibFile(ConfigError):
    """No references.bib exists yet."""

    def __init__(self, bib_path: str):
        super().__init__(
            f"Bibliography file not found at {bib_path}",
            hint=(
                "The library is empty. Use paper(key='...', title='...') to create "
                "the first entry, or place a PDF in tome/inbox/ and run ingest. "
                "See guide('paper-workflow') for the full pipeline."
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
                "Directories .tome-mcp/, .git/, .venv/ are always excluded. "
                "See guide('configuration') for details."
            ),
        )


class ChromaDBError(TomeError):
    """ChromaDB initialization or query failed."""

    def __init__(self, detail: str):
        super().__init__(
            f"ChromaDB error: {detail}. "
            f"Paper chunks live in ~/.tome-mcp/chroma/ (vault), "
            f"corpus chunks in .tome-mcp/chroma/ (project). "
            f"Try: reindex(scope='all') to rebuild. "
            f"If that fails, delete the relevant chroma/ dir and reindex again. "
            f"If this persists, use report_issue to log it — see guide('reporting-issues')."
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
                "Unpaywall requires an email for API access (they don't spam). "
                "See guide('configuration') for details."
            ),
        )
