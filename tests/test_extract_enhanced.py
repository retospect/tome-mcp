"""Tests for enhanced PDF extraction — XMP, font-size title, text metrics."""

import xml.etree.ElementTree as ET

from tome.extract import (
    TextMetrics,
    XMPMetadata,
    _classify_paper_type,
    _parse_xmp_xml,
    compute_text_metrics,
)


# ---------------------------------------------------------------------------
# XMP parsing
# ---------------------------------------------------------------------------

SAMPLE_XMP = """\
<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Metal-Organic Frameworks for Electronic Devices</rdf:li>
        </rdf:Alt>
      </dc:title>
      <dc:creator>
        <rdf:Seq>
          <rdf:li>Xu, Yang</rdf:li>
          <rdf:li>Guo, Xuefeng</rdf:li>
        </rdf:Seq>
      </dc:creator>
      <dc:description>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">We report a new conductive MOF...</rdf:li>
        </rdf:Alt>
      </dc:description>
      <dc:subject>
        <rdf:Bag>
          <rdf:li>MOF</rdf:li>
          <rdf:li>conductivity</rdf:li>
          <rdf:li>electronics</rdf:li>
        </rdf:Bag>
      </dc:subject>
      <prism:doi>10.1021/jacs.5b00672</prism:doi>
      <prism:publicationName>Journal of the American Chemical Society</prism:publicationName>
      <prism:coverDate>2015-04-01</prism:coverDate>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""


class TestXMPParsing:
    def test_parse_title(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.dc_title == "Metal-Organic Frameworks for Electronic Devices"

    def test_parse_creators(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.dc_creator == ["Xu, Yang", "Guo, Xuefeng"]

    def test_parse_description(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.dc_description == "We report a new conductive MOF..."

    def test_parse_subjects(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.dc_subject == ["MOF", "conductivity", "electronics"]

    def test_parse_doi(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.prism_doi == "10.1021/jacs.5b00672"

    def test_parse_journal(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.prism_publication == "Journal of the American Chemical Society"

    def test_parse_date(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.prism_cover_date == "2015-04-01"

    def test_raw_xml_stored(self):
        meta = _parse_xmp_xml(SAMPLE_XMP)
        assert meta.raw_xml is not None
        assert "jacs.5b00672" in meta.raw_xml

    def test_empty_xml(self):
        meta = _parse_xmp_xml("")
        assert meta.dc_title is None
        assert meta.dc_creator == []

    def test_invalid_xml(self):
        meta = _parse_xmp_xml("<not valid xml")
        assert meta.dc_title is None

    def test_minimal_xmp(self):
        xml = """\
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Simple Title</rdf:li>
        </rdf:Alt>
      </dc:title>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>"""
        meta = _parse_xmp_xml(xml)
        assert meta.dc_title == "Simple Title"
        assert meta.prism_doi is None
        assert meta.dc_creator == []


# ---------------------------------------------------------------------------
# Text metrics
# ---------------------------------------------------------------------------


class TestTextMetrics:
    def test_word_count(self):
        pages = ["Hello world foo bar", "baz quux another word"]
        m = compute_text_metrics(pages)
        assert m.word_count == 8

    def test_ref_count_numbered(self):
        pages = ["As shown [1], confirmed by [2] and [42]."]
        m = compute_text_metrics(pages)
        assert m.ref_count == 42

    def test_ref_count_author_year(self):
        pages = ["(Smith, 2020) showed that (Jones et al., 2019) agreed."]
        m = compute_text_metrics(pages)
        assert m.ref_count == 2

    def test_figure_count(self):
        pages = ["See Figure 1 and Fig. 2. Also Figure 1 again. And Figure 3."]
        m = compute_text_metrics(pages)
        assert m.figure_count == 3

    def test_table_count(self):
        pages = ["Table 1 shows results. See also Table 2."]
        m = compute_text_metrics(pages)
        assert m.table_count == 2

    def test_abstract_extraction(self):
        pages = [
            "Some header\n\nAbstract\n\nThis paper describes a new method.\n\n"
            "Introduction\n\nWe begin by..."
        ]
        m = compute_text_metrics(pages)
        assert m.has_abstract is True
        assert "new method" in m.abstract_text

    def test_no_abstract(self):
        pages = ["Just body text with no abstract heading."]
        m = compute_text_metrics(pages)
        assert m.has_abstract is False
        assert m.abstract_text is None

    def test_text_quality_ascii(self):
        pages = ["Pure ASCII text here."]
        m = compute_text_metrics(pages)
        assert m.text_quality > 0.9

    def test_text_quality_unicode(self):
        pages = ["日本語のテキスト" * 100]
        m = compute_text_metrics(pages)
        assert m.text_quality < 0.5

    def test_extractable_pages(self):
        good = "This is a page with enough content to be considered extractable text. " * 2
        pages = [good, "", "   ", good]
        m = compute_text_metrics(pages)
        assert m.extractable_pages == 2

    def test_empty_pages(self):
        m = compute_text_metrics([])
        assert m.word_count == 0
        assert m.ref_count == 0
        assert m.text_quality == 0.0


class TestPaperTypeClassification:
    def test_letter(self):
        assert _classify_paper_type(page_count=3, word_count=2000, ref_count=15) == "letter"

    def test_review(self):
        assert _classify_paper_type(page_count=30, word_count=15000, ref_count=150) == "review"

    def test_article(self):
        assert _classify_paper_type(page_count=10, word_count=6000, ref_count=40) == "article"

    def test_borderline_letter(self):
        assert _classify_paper_type(page_count=4, word_count=3999, ref_count=20) == "letter"

    def test_borderline_not_letter(self):
        assert _classify_paper_type(page_count=5, word_count=4000, ref_count=20) == "article"
