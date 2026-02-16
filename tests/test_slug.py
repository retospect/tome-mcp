"""Tests for slug generation from paper titles."""

from tome.slug import make_key, slug_from_title


class TestSlugFromTitle:
    def test_long_first_word(self):
        assert slug_from_title("Conductivity in Metal-Organic Frameworks") == "conductivity"

    def test_two_short_words(self):
        assert slug_from_title("Folding DNA to Create Nanoscale Shapes") == "foldingdna"

    def test_stopwords_filtered(self):
        assert slug_from_title("A Novel Approach for the Study of MOFs") == "approach"

    def test_unicode_normalized(self):
        assert slug_from_title("Métallo-Organic Réseau Conducteurs") == "metalloorganic"

    def test_empty_title(self):
        assert slug_from_title("") == ""

    def test_all_stopwords(self):
        assert slug_from_title("A The Of In On And") == ""

    def test_single_word(self):
        assert slug_from_title("Rotaxanes") == "rotaxanes"

    def test_short_words_combine(self):
        assert slug_from_title("DNA Logic Gates") == "dnalogic"

    def test_numbers_excluded(self):
        # Only alpha words of 3+ chars
        assert slug_from_title("Ni3(HITP)2: A New MOF") == "hitpmof"

    def test_real_title_sheberla(self):
        slug = slug_from_title("High Electrical Conductivity in Ni3(HITP)2, a Semiconducting MOF")
        assert slug == "highelectrical"

    def test_real_title_rothemund(self):
        slug = slug_from_title("Folding DNA to Create Nanoscale Shapes and Patterns")
        assert slug == "foldingdna"

    def test_real_title_qian(self):
        slug = slug_from_title("Scaling Up Digital Circuit Computation with DNA Strand Displacement Cascades")
        assert slug == "scalingdigital"

    def test_real_title_collier(self):
        slug = slug_from_title("Electronically Configurable Molecular-Based Logic Gates")
        assert slug == "electronically"

    def test_max_words_override(self):
        slug = slug_from_title("DNA Logic Gates", max_words=3)
        assert slug == "dnalogicgates"

    def test_hyphens_split(self):
        slug = slug_from_title("Metal-Organic Framework Conductors")
        assert slug == "metalorganic"


class TestMakeKey:
    def test_basic(self):
        key = make_key("Miller", 1999, "Molecular Logic Gates")
        assert key == "miller1999molecular"

    def test_de_silva(self):
        key = make_key("de Silva", 2007, "Molecular Logic and Computing")
        assert key == "desilva2007molecular"

    def test_hyphenated_surname(self):
        key = make_key("Ben-Gurion", 2020, "Quantum Computing Methods")
        assert key == "bengurion2020quantumcomputing"

    def test_unicode_surname(self):
        key = make_key("Müller", 2015, "Framework Synthesis")
        assert key == "muller2015framework"

    def test_year_as_string(self):
        key = make_key("Smith", "2024", "DNA Nanotechnology")
        assert key == "smith2024dnananotechnology"

    def test_apostrophe_surname(self):
        key = make_key("O'Brien", 2018, "Crystal Engineering")
        assert key == "obrien2018crystalengineering"
