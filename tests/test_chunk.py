"""Tests for tome.chunk."""

from tome.chunk import chunk_text


class TestChunkText:
    def test_empty_string(self):
        assert chunk_text("") == []

    def test_whitespace_only(self):
        assert chunk_text("   \n\t  ") == []

    def test_single_short_sentence(self):
        result = chunk_text("Hello world.", max_chars=500)
        assert result == ["Hello world."]

    def test_single_sentence_no_period(self):
        result = chunk_text("Hello world", max_chars=500)
        assert result == ["Hello world"]

    def test_two_sentences_fit_in_one_chunk(self):
        text = "First sentence. Second sentence."
        result = chunk_text(text, max_chars=500)
        assert len(result) == 1
        assert "First sentence." in result[0]
        assert "Second sentence." in result[0]

    def test_sentences_split_across_chunks(self):
        text = "Short one. " * 20  # ~220 chars
        result = chunk_text(text.strip(), max_chars=100, overlap=20)
        assert len(result) > 1
        # Each chunk should be under or near max_chars
        for chunk in result:
            # Allow some slack for sentence boundaries
            assert len(chunk) < 150

    def test_overlap_creates_shared_content(self):
        sentences = [f"Sentence number {i} here." for i in range(10)]
        text = " ".join(sentences)
        result = chunk_text(text, max_chars=100, overlap=50)
        assert len(result) >= 2
        # Check that consecutive chunks share some text
        for i in range(len(result) - 1):
            words_a = set(result[i].split())
            words_b = set(result[i + 1].split())
            assert words_a & words_b, f"Chunks {i} and {i + 1} share no words"

    def test_very_long_sentence_emitted_alone(self):
        long = "x" * 600
        text = f"Short. {long} End."
        result = chunk_text(text, max_chars=500, overlap=50)
        # The long sentence should appear as its own chunk
        assert any(long in chunk for chunk in result)

    def test_no_duplicate_final_chunk(self):
        text = "One. Two. Three."
        result = chunk_text(text, max_chars=500)
        assert len(result) == len(set(result))

    def test_preserves_sentence_content(self):
        text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
        result = chunk_text(text, max_chars=500)
        combined = " ".join(result)
        assert "Alpha beta gamma." in combined
        assert "Delta epsilon zeta." in combined
        assert "Eta theta iota." in combined

    def test_default_parameters(self):
        text = "A. " * 200  # ~600 chars
        result = chunk_text(text.strip())
        # Should produce multiple chunks with default 500/100
        assert len(result) >= 2

    def test_question_marks_split(self):
        text = "What is this? It is a test. Really? Yes."
        result = chunk_text(text, max_chars=30, overlap=10)
        assert len(result) >= 2

    def test_exclamation_marks_split(self):
        text = "Wow! Amazing! Incredible! Fantastic! Spectacular!"
        result = chunk_text(text, max_chars=20, overlap=5)
        assert len(result) >= 2

    def test_returns_list_of_strings(self):
        result = chunk_text("Hello. World.")
        assert isinstance(result, list)
        assert all(isinstance(c, str) for c in result)
