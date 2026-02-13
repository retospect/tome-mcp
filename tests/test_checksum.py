"""Tests for tome.checksum."""

import hashlib
from pathlib import Path

import pytest

from tome.checksum import sha256_bytes, sha256_file


class TestSha256File:
    def test_known_content(self, tmp_path: Path):
        p = tmp_path / "test.txt"
        p.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_file(p) == expected

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_file(p) == expected

    def test_binary_file(self, tmp_path: Path):
        data = bytes(range(256)) * 100
        p = tmp_path / "binary.bin"
        p.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_file(p) == expected

    def test_large_file_spans_chunks(self, tmp_path: Path):
        data = b"x" * 200_000  # larger than CHUNK_SIZE (64KB)
        p = tmp_path / "large.bin"
        p.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_file(p) == expected

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            sha256_file(tmp_path / "nonexistent.txt")

    def test_directory_raises(self, tmp_path: Path):
        with pytest.raises(IsADirectoryError):
            sha256_file(tmp_path)

    def test_digest_format(self, tmp_path: Path):
        p = tmp_path / "test.txt"
        p.write_bytes(b"test")
        digest = sha256_file(p)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_different_content_different_hash(self, tmp_path: Path):
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        p1.write_bytes(b"hello")
        p2.write_bytes(b"world")
        assert sha256_file(p1) != sha256_file(p2)

    def test_same_content_same_hash(self, tmp_path: Path):
        p1 = tmp_path / "a.txt"
        p2 = tmp_path / "b.txt"
        p1.write_bytes(b"same")
        p2.write_bytes(b"same")
        assert sha256_file(p1) == sha256_file(p2)


class TestSha256Bytes:
    def test_known_content(self):
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_bytes(b"hello world") == expected

    def test_empty_bytes(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_bytes(b"") == expected

    def test_consistent_with_file(self, tmp_path: Path):
        data = b"consistency check"
        p = tmp_path / "test.txt"
        p.write_bytes(data)
        assert sha256_bytes(data) == sha256_file(p)
