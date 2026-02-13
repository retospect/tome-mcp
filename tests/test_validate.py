"""Tests for tome.validate."""

import pytest
from pathlib import Path

from tome.errors import UnsafeInput
from tome.validate import validate_key, validate_relative_path, ensure_within


class TestValidateKey:
    def test_simple_key(self):
        assert validate_key("xu2022") == "xu2022"

    def test_key_with_suffix(self):
        assert validate_key("xu2022a") == "xu2022a"

    def test_key_with_hyphen(self):
        assert validate_key("wang-2025") == "wang-2025"

    def test_key_with_underscore(self):
        assert validate_key("us8082535") == "us8082535"

    def test_key_with_dot(self):
        assert validate_key("thorlabs_m365l4") == "thorlabs_m365l4"

    def test_empty_key(self):
        with pytest.raises(UnsafeInput, match="must not be empty"):
            validate_key("")

    def test_null_byte(self):
        with pytest.raises(UnsafeInput, match="null byte"):
            validate_key("xu\x002022")

    def test_path_traversal(self):
        with pytest.raises(UnsafeInput, match="path traversal"):
            validate_key("../../etc/passwd")

    def test_dotdot_in_key(self):
        with pytest.raises(UnsafeInput, match="path traversal"):
            validate_key("key..traversal")

    def test_slash_in_key(self):
        with pytest.raises(UnsafeInput, match="path separator"):
            validate_key("key/traversal")

    def test_backslash_in_key(self):
        with pytest.raises(UnsafeInput, match="path separator"):
            validate_key("key\\traversal")

    def test_space_in_key(self):
        with pytest.raises(UnsafeInput, match="must start with alphanumeric"):
            validate_key("xu 2022")

    def test_starts_with_dot(self):
        with pytest.raises(UnsafeInput, match="must start with alphanumeric"):
            validate_key(".hidden")

    def test_starts_with_hyphen(self):
        with pytest.raises(UnsafeInput, match="must start with alphanumeric"):
            validate_key("-flag")

    def test_too_long(self):
        with pytest.raises(UnsafeInput, match="exceeds"):
            validate_key("a" * 101)

    def test_max_length_ok(self):
        assert validate_key("a" * 100) == "a" * 100


class TestValidateRelativePath:
    def test_simple(self):
        assert validate_relative_path("inbox/paper.pdf") == "inbox/paper.pdf"

    def test_nested(self):
        assert validate_relative_path("a/b/c.pdf") == "a/b/c.pdf"

    def test_empty(self):
        with pytest.raises(UnsafeInput, match="must not be empty"):
            validate_relative_path("")

    def test_absolute(self):
        with pytest.raises(UnsafeInput, match="absolute"):
            validate_relative_path("/etc/passwd")

    def test_traversal(self):
        with pytest.raises(UnsafeInput, match="path traversal"):
            validate_relative_path("../../../etc/passwd")

    def test_null_byte(self):
        with pytest.raises(UnsafeInput, match="null byte"):
            validate_relative_path("file\x00.pdf")

    def test_traversal_in_middle(self):
        with pytest.raises(UnsafeInput, match="path traversal"):
            validate_relative_path("inbox/../../secret.pdf")


class TestEnsureWithin:
    def test_within(self, tmp_path):
        child = tmp_path / "subdir" / "file.txt"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()
        assert ensure_within(child, tmp_path) == child

    def test_escape(self, tmp_path):
        escaped = tmp_path / ".." / "other"
        with pytest.raises(UnsafeInput, match="resolves outside"):
            ensure_within(escaped, tmp_path)

    def test_symlink_escape(self, tmp_path):
        target = tmp_path.parent / "secret"
        target.mkdir(exist_ok=True)
        link = tmp_path / "link"
        link.symlink_to(target)
        with pytest.raises(UnsafeInput, match="resolves outside"):
            ensure_within(link, tmp_path)
