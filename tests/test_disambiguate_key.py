"""Tests for _disambiguate_key — key collision handling during ingest."""

import pytest

from tome.server import _disambiguate_key


class TestDisambiguateKey:
    def test_appends_a_suffix(self):
        assert _disambiguate_key("xu2022", {"xu2022"}) == "xu2022a"

    def test_skips_taken_suffixes(self):
        existing = {"chen2022photochromic", "chen2022photochromica", "chen2022photochromicb"}
        assert _disambiguate_key("chen2022photochromic", existing) == "chen2022photochromicc"

    def test_all_26_suffixes_exhausted(self):
        existing = {"k2024"} | {f"k2024{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
        with pytest.raises(ValueError, match="Exhausted"):
            _disambiguate_key("k2024", existing)

    def test_key_not_in_existing_is_not_called(self):
        # _disambiguate_key is only called when key IS in existing,
        # so it always appends a suffix — even if key itself is free,
        # the caller gates entry.
        result = _disambiguate_key("free2024", {"other2024"})
        assert result == "free2024a"
