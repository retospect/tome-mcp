"""Tests for tome.errors — exception hierarchy and LLM-readable messages.

Every error must:
1. Be a subclass of TomeError
2. Store structured attributes for programmatic access
3. Format a message that tells the LLM what happened and how to fix it
"""

import pytest

from tome.errors import (
    APIError,
    BibParseError,
    BibWriteError,
    ChromaDBError,
    ConfigError,
    DOIResolutionFailed,
    DuplicateKey,
    FigureNotFound,
    IngestFailed,
    NoBibFile,
    NoTexFiles,
    PageOutOfRange,
    PaperNotFound,
    RootFileNotFound,
    RootNotFound,
    TextNotExtracted,
    TomeError,
    UnpaywallNotConfigured,
    UnsafeInput,
)

# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TestHierarchy:
    def test_all_inherit_tome_error(self):
        classes = [
            PaperNotFound,
            PageOutOfRange,
            DuplicateKey,
            DOIResolutionFailed,
            IngestFailed,
            BibParseError,
            BibWriteError,
            FigureNotFound,
            TextNotExtracted,
            APIError,
            UnsafeInput,
            ConfigError,
            RootNotFound,
            RootFileNotFound,
            NoBibFile,
            NoTexFiles,
            ChromaDBError,
            UnpaywallNotConfigured,
        ]
        for cls in classes:
            assert issubclass(cls, TomeError), f"{cls.__name__} not a TomeError subclass"

    def test_config_subtypes(self):
        config_classes = [
            RootNotFound,
            RootFileNotFound,
            NoBibFile,
            NoTexFiles,
            UnpaywallNotConfigured,
        ]
        for cls in config_classes:
            assert issubclass(cls, ConfigError), f"{cls.__name__} not a ConfigError subclass"

    def test_tome_error_is_exception(self):
        assert issubclass(TomeError, Exception)


# ---------------------------------------------------------------------------
# Original exceptions — attributes and message content
# ---------------------------------------------------------------------------


class TestPaperNotFound:
    def test_attributes(self):
        e = PaperNotFound("xu2022")
        assert e.key == "xu2022"
        assert e.near == []

    def test_message_contains_key_and_hint(self):
        e = PaperNotFound("xu2022")
        msg = str(e)
        assert "xu2022" in msg
        assert "paper(action='list')" in msg
        assert "search(query=" in msg

    def test_near_suggestions(self):
        e = PaperNotFound("xu2022", near=["xu2022mof", "xu2023"])
        msg = str(e)
        assert "Similar keys" in msg
        assert "xu2022mof" in msg
        assert "xu2023" in msg
        assert e.near == ["xu2022mof", "xu2023"]

    def test_near_capped_at_five(self):
        e = PaperNotFound("k", near=["a", "b", "c", "d", "e", "f"])
        msg = str(e)
        assert "'f'" not in msg  # only first 5 shown


class TestPageOutOfRange:
    def test_attributes(self):
        e = PageOutOfRange("xu2022", 15, 12)
        assert e.key == "xu2022"
        assert e.page == 15
        assert e.total == 12

    def test_message(self):
        msg = str(PageOutOfRange("xu2022", 15, 12))
        assert "12 pages" in msg
        assert "page 15" in msg
        assert "1-12" in msg

    def test_page_zero_hint(self):
        msg = str(PageOutOfRange("xu2022", 0, 12))
        assert "1-indexed" in msg
        assert "page=1" in msg

    def test_negative_page_hint(self):
        msg = str(PageOutOfRange("xu2022", -1, 12))
        assert "1-indexed" in msg


class TestDuplicateKey:
    def test_attributes(self):
        e = DuplicateKey("miller1999")
        assert e.key == "miller1999"

    def test_message_suggests_suffix(self):
        msg = str(DuplicateKey("miller1999"))
        assert "miller1999a" in msg


class TestDOIResolutionFailed:
    def test_404(self):
        e = DOIResolutionFailed("10.1/fake", 404)
        assert e.doi == "10.1/fake"
        assert e.status_code == 404
        assert "hallucinated" in str(e)
        assert "discover(query=" in str(e)
        assert "doi(action='reject'" in str(e)

    def test_429(self):
        e = DOIResolutionFailed("10.1/x", 429)
        assert "rate-limited" in str(e)

    def test_500(self):
        e = DOIResolutionFailed("10.1/x", 503)
        assert "503" in str(e)
        assert "transient" in str(e)


class TestIngestFailed:
    def test_attributes(self):
        e = IngestFailed("inbox/paper.pdf", "no DOI found")
        assert e.path == "inbox/paper.pdf"
        assert e.reason == "no DOI found"

    def test_message_suggests_manual_key(self):
        msg = str(IngestFailed("inbox/paper.pdf", "no DOI"))
        assert "authorYYYY" in msg
        assert "inbox/" in msg


class TestBibParseError:
    def test_attributes(self):
        e = BibParseError("tome/references.bib", "unmatched brace at line 42")
        assert e.path == "tome/references.bib"
        assert e.detail == "unmatched brace at line 42"
        assert "not modified" in str(e)
        assert "git diff" in str(e)


class TestBibWriteError:
    def test_message_mentions_backup(self):
        e = BibWriteError("tome/references.bib", "entry count changed")
        assert "backup" in str(e)
        assert "not modified" in str(e)


class TestFigureNotFound:
    def test_attributes(self):
        e = FigureNotFound("xu2022", "fig3")
        assert e.key == "xu2022"
        assert e.figure == "fig3"
        assert "figure(key=" in str(e)


class TestTextNotExtracted:
    def test_message_suggests_reindex(self):
        e = TextNotExtracted("xu2022")
        assert e.key == "xu2022"
        assert e.has_pdf is None
        assert "reindex" in str(e)

    def test_has_pdf_true(self):
        e = TextNotExtracted("xu2022", has_pdf=True)
        assert "PDF exists" in str(e)
        assert "reindex" in str(e)

    def test_has_pdf_false(self):
        e = TextNotExtracted("xu2022", has_pdf=False)
        assert "No PDF" in str(e)
        assert "doi(key='xu2022', action='fetch')" in str(e)
        assert "inbox" in str(e)


class TestAPIError:
    def test_429(self):
        e = APIError("Semantic Scholar", 429)
        assert e.service == "Semantic Scholar"
        assert e.status_code == 429
        assert "rate-limited" in str(e)

    def test_500(self):
        e = APIError("CrossRef", 502, "bad gateway")
        assert "server error" in str(e)
        assert "bad gateway" in str(e)

    def test_timeout(self):
        e = APIError("Unpaywall", 0)
        assert "unreachable" in str(e)

    def test_other(self):
        e = APIError("CrossRef", 403, "forbidden")
        assert "403" in str(e)


class TestUnsafeInput:
    def test_attributes(self):
        e = UnsafeInput("key", "../etc/passwd", "path traversal")
        assert e.field == "key"
        assert e.value == "../etc/passwd"
        assert e.reason == "path traversal"
        assert "unsafe" in str(e).lower()


# ---------------------------------------------------------------------------
# New config/infrastructure exceptions
# ---------------------------------------------------------------------------


class TestConfigError:
    def test_with_hint(self):
        e = ConfigError("bad value", hint="Fix it like this.")
        assert e.detail == "bad value"
        assert e.hint == "Fix it like this."
        msg = str(e)
        assert "bad value" in msg
        assert "Fix it like this." in msg

    def test_without_hint(self):
        e = ConfigError("something wrong")
        assert e.hint == ""
        msg = str(e)
        assert "something wrong" in msg


class TestRootNotFound:
    def test_with_available(self):
        e = RootNotFound("thesis", ["default", "talk"])
        assert isinstance(e, ConfigError)
        msg = str(e)
        assert "thesis" in msg
        assert "'default'" in msg
        assert "'talk'" in msg
        assert "roots:" in msg

    def test_empty_available(self):
        e = RootNotFound("thesis", [])
        assert "(none defined)" in str(e)


class TestRootFileNotFound:
    def test_message(self):
        e = RootFileNotFound("default", "main.tex", "/home/user/project")
        assert isinstance(e, ConfigError)
        msg = str(e)
        assert "default" in msg
        assert "main.tex" in msg
        assert "/home/user/project" in msg
        assert "config.yaml" in msg


class TestNoBibFile:
    def test_message_suggests_paper(self):
        e = NoBibFile("/project/tome/references.bib")
        assert isinstance(e, ConfigError)
        msg = str(e)
        assert "references.bib" in msg
        assert "paper(key=" in msg
        assert "ingest" in msg


class TestNoTexFiles:
    def test_message_lists_globs(self):
        e = NoTexFiles(["sections/*.tex", "chapters/*.tex"])
        assert isinstance(e, ConfigError)
        msg = str(e)
        assert "sections/*.tex" in msg
        assert "chapters/*.tex" in msg
        assert "tex_globs" in msg


class TestChromaDBError:
    def test_message_suggests_reindex(self):
        e = ChromaDBError("collection not found")
        assert isinstance(e, TomeError)
        assert e.detail == "collection not found"
        msg = str(e)
        assert "collection not found" in msg
        assert "reindex" in msg
        assert ".tome-mcp/chroma/" in msg


class TestUnpaywallNotConfigured:
    def test_message(self):
        e = UnpaywallNotConfigured()
        assert isinstance(e, ConfigError)
        msg = str(e)
        assert "UNPAYWALL_EMAIL" in msg
        assert "unpaywall_email" in msg
        assert "config.yaml" in msg


# ---------------------------------------------------------------------------
# Catch-all: every exception can be raised and caught as TomeError
# ---------------------------------------------------------------------------


class TestCatchAll:
    """Verify every exception can be raised/caught in a single handler."""

    @pytest.mark.parametrize(
        "exc",
        [
            PaperNotFound("k"),
            PageOutOfRange("k", 5, 3),
            DuplicateKey("k"),
            DOIResolutionFailed("10.1/x", 404),
            IngestFailed("p.pdf", "reason"),
            BibParseError("f.bib", "detail"),
            BibWriteError("f.bib", "detail"),
            FigureNotFound("k", "fig1"),
            TextNotExtracted("k"),
            APIError("svc", 500),
            UnsafeInput("field", "val", "reason"),
            ConfigError("detail"),
            RootNotFound("r", []),
            RootFileNotFound("r", "f.tex", "/root"),
            NoBibFile("/bib"),
            NoTexFiles(["*.tex"]),
            ChromaDBError("detail"),
            UnpaywallNotConfigured(),
        ],
    )
    def test_caught_as_tome_error(self, exc):
        with pytest.raises(TomeError):
            raise exc

    @pytest.mark.parametrize(
        "exc",
        [
            RootNotFound("r", []),
            RootFileNotFound("r", "f.tex", "/root"),
            NoBibFile("/bib"),
            NoTexFiles(["*.tex"]),
            UnpaywallNotConfigured(),
        ],
    )
    def test_caught_as_config_error(self, exc):
        with pytest.raises(ConfigError):
            raise exc
