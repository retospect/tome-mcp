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


class OllamaUnavailable(TomeError):
    """Cannot reach the Ollama embedding server."""

    def __init__(self, url: str):
        super().__init__(
            f"Cannot reach Ollama at {url}. "
            f"Ensure Ollama is running ('ollama serve') and the URL is correct. "
            f"Ingest will still extract text but skip embedding. "
            f"Run rebuild after starting Ollama to generate embeddings."
        )
        self.url = url


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
