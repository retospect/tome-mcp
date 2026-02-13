"""Tests for tome.latex."""

from tome.latex import ChunkMarkers, extract_markers, find_cite_locations


class TestExtractMarkers:
    def test_label(self):
        text = r"Some text \label{sec:intro} more text"
        m = extract_markers(text)
        assert m.labels == ["sec:intro"]
        assert m.has_label is True

    def test_multiple_labels(self):
        text = r"\label{fig:one} text \label{fig:two}"
        m = extract_markers(text)
        assert m.labels == ["fig:one", "fig:two"]

    def test_ref(self):
        text = r"See Section~\ref{sec:intro} for details"
        m = extract_markers(text)
        assert m.refs == ["sec:intro"]
        assert m.has_ref is True

    def test_eqref(self):
        text = r"Equation~\eqref{eq:conductance}"
        m = extract_markers(text)
        assert m.refs == ["eq:conductance"]

    def test_autoref(self):
        text = r"\autoref{tab:results}"
        m = extract_markers(text)
        assert m.refs == ["tab:results"]

    def test_cref(self):
        text = r"\cref{sec:methods}"
        m = extract_markers(text)
        assert m.refs == ["sec:methods"]

    def test_cite_single(self):
        text = r"\cite{xu2022}"
        m = extract_markers(text)
        assert m.cites == ["xu2022"]
        assert m.has_cite is True

    def test_cite_multiple_keys(self):
        text = r"\cite{xu2022,chen2023,lambert2015}"
        m = extract_markers(text)
        assert m.cites == ["xu2022", "chen2023", "lambert2015"]

    def test_citep(self):
        text = r"\citep{xu2022}"
        m = extract_markers(text)
        assert m.cites == ["xu2022"]

    def test_citet(self):
        text = r"\citet{chen2023}"
        m = extract_markers(text)
        assert m.cites == ["chen2023"]

    def test_mciteboxp(self):
        text = r"\mciteboxp{xu2022}{3}{some quote}"
        m = extract_markers(text)
        assert "xu2022" in m.cites

    def test_section(self):
        text = r"\section{Signal Domains}"
        m = extract_markers(text)
        assert m.sections == ["Signal Domains"]
        assert m.has_section is True

    def test_subsection(self):
        text = r"\subsection{Quantum Interference}"
        m = extract_markers(text)
        assert m.sections == ["Quantum Interference"]

    def test_star_section(self):
        text = r"\section*{Acknowledgements}"
        m = extract_markers(text)
        assert m.sections == ["Acknowledgements"]

    def test_no_markers(self):
        text = "Plain text with no LaTeX markers at all."
        m = extract_markers(text)
        assert m.has_label is False
        assert m.has_ref is False
        assert m.has_cite is False
        assert m.has_section is False

    def test_deduplicate_cites(self):
        text = r"\cite{xu2022} and later \cite{xu2022}"
        m = extract_markers(text)
        assert m.cites == ["xu2022"]

    def test_deduplicate_refs(self):
        text = r"\ref{sec:intro} then \ref{sec:intro}"
        m = extract_markers(text)
        assert m.refs == ["sec:intro"]

    def test_mixed(self):
        text = (
            r"\section{Results}"
            "\n"
            r"\label{sec:results}"
            "\n"
            r"As shown by \citet{xu2022}, the interference pattern "
            r"(see Figure~\ref{fig:qi}) confirms the prediction "
            r"from Section~\ref{sec:theory} \cite{lambert2015,chen2023}."
        )
        m = extract_markers(text)
        assert m.labels == ["sec:results"]
        assert "sec:results" not in m.refs  # label != ref
        assert "fig:qi" in m.refs
        assert "sec:theory" in m.refs
        assert "xu2022" in m.cites
        assert "lambert2015" in m.cites
        assert "chen2023" in m.cites
        assert m.sections == ["Results"]


class TestChunkMarkersToMetadata:
    def test_empty(self):
        m = ChunkMarkers()
        meta = m.to_metadata()
        assert meta["has_label"] is False
        assert meta["has_ref"] is False
        assert meta["has_cite"] is False
        assert meta["has_section"] is False
        assert "labels" not in meta
        assert "cites" not in meta

    def test_with_data(self):
        m = ChunkMarkers(
            labels=["sec:intro"],
            cites=["xu2022", "chen2023"],
            refs=["fig:one"],
            sections=["Introduction"],
        )
        meta = m.to_metadata()
        assert meta["has_label"] is True
        assert meta["labels"] == "sec:intro"
        assert meta["has_cite"] is True
        assert meta["cites"] == "xu2022,chen2023"
        assert meta["has_ref"] is True
        assert meta["refs"] == "fig:one"
        assert meta["has_section"] is True
        assert meta["sections"] == "Introduction"


class TestFindCiteLocations:
    def test_finds_cite(self, tmp_path):
        tex = tmp_path / "a.tex"
        tex.write_text("Some text \\cite{xu2022} here.\n")
        results = find_cite_locations("xu2022", [tex])
        assert len(results) == 1
        assert results[0]["file"] == str(tex)
        assert results[0]["line"] == 1
        assert "xu2022" in results[0]["command"]

    def test_multi_key_cite(self, tmp_path):
        tex = tmp_path / "b.tex"
        tex.write_text("See \\cite{xu2022, chen2023, miller1999} for details.\n")
        results = find_cite_locations("chen2023", [tex])
        assert len(results) == 1
        assert results[0]["line"] == 1

    def test_citep_citet(self, tmp_path):
        tex = tmp_path / "c.tex"
        tex.write_text("Result \\citep{xu2022}.\nAlso \\citet{xu2022} showed.\n")
        results = find_cite_locations("xu2022", [tex])
        assert len(results) == 2
        assert results[0]["line"] == 1
        assert results[1]["line"] == 2

    def test_mciteboxp(self, tmp_path):
        tex = tmp_path / "d.tex"
        tex.write_text("\\mciteboxp{xu2022}{42}{A quote.}\n")
        results = find_cite_locations("xu2022", [tex])
        assert len(results) == 1

    def test_key_not_found(self, tmp_path):
        tex = tmp_path / "e.tex"
        tex.write_text("\\cite{other2022}\n")
        results = find_cite_locations("xu2022", [tex])
        assert len(results) == 0

    def test_multiple_files(self, tmp_path):
        a = tmp_path / "a.tex"
        b = tmp_path / "b.tex"
        a.write_text("\\cite{xu2022}\n")
        b.write_text("Line one.\n\\citep{xu2022}\n")
        results = find_cite_locations("xu2022", [a, b])
        assert len(results) == 2
        assert results[0]["file"] == str(a)
        assert results[1]["file"] == str(b)
        assert results[1]["line"] == 2

    def test_no_false_positive_on_substring(self, tmp_path):
        tex = tmp_path / "f.tex"
        tex.write_text("\\cite{xu2022a}\n")
        results = find_cite_locations("xu2022", [tex])
        assert len(results) == 0
