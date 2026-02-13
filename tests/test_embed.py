"""Tests for tome.embed.

Unit tests mock httpx. Integration tests (marked) require a running Ollama.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from tome.embed import (
    check_ollama,
    embed_single,
    embed_texts,
    get_embed_model,
    get_ollama_url,
    load_embeddings,
    save_embeddings,
)
from tome.errors import OllamaUnavailable


class TestConfig:
    def test_default_url(self):
        with patch.dict("os.environ", {}, clear=True):
            assert "localhost" in get_ollama_url()

    def test_custom_url(self):
        with patch.dict("os.environ", {"TOME_OLLAMA_URL": "http://myhost:1234"}):
            assert get_ollama_url() == "http://myhost:1234"

    def test_default_model(self):
        with patch.dict("os.environ", {}, clear=True):
            assert get_embed_model() == "nomic-embed-text"

    def test_custom_model(self):
        with patch.dict("os.environ", {"TOME_EMBED_MODEL": "custom-model"}):
            assert get_embed_model() == "custom-model"


class TestCheckOllama:
    def test_unreachable(self):
        assert check_ollama("http://localhost:99999") is False


class TestEmbedTexts:
    def test_connection_error_raises(self):
        with pytest.raises(OllamaUnavailable):
            embed_texts(["hello"], url="http://localhost:99999")

    @patch("tome.embed.httpx.post")
    def test_successful_embedding(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        }
        result = embed_texts(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 3)

    @patch("tome.embed.httpx.post")
    def test_truncates_long_text(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"embeddings": [[0.1]]}
        embed_texts(["x" * 10000])
        call_args = mock_post.call_args
        sent_text = call_args.kwargs["json"]["input"][0]
        assert len(sent_text) <= 3500

    @patch("tome.embed.httpx.post")
    def test_batching(self, mock_post):
        # 20 texts should be split into 2 batches (BATCH_SIZE=16)
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"embeddings": [[0.1]] * 16}

        # First call gets 16, second gets 4
        def side_effect(*args, **kwargs):
            batch = kwargs["json"]["input"]
            resp = type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {"embeddings": [[0.1]] * len(batch)},
                },
            )()
            return resp

        mock_post.side_effect = side_effect
        result = embed_texts([f"text{i}" for i in range(20)])
        assert result.shape == (20, 1)
        assert mock_post.call_count == 2

    @patch("tome.embed.httpx.post")
    def test_non_200_raises(self, mock_post):
        mock_post.return_value.status_code = 500
        with pytest.raises(OllamaUnavailable):
            embed_texts(["hello"])


class TestEmbedSingle:
    @patch("tome.embed.httpx.post")
    def test_returns_1d(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"embeddings": [[0.1, 0.2]]}
        result = embed_single("hello")
        assert result.shape == (2,)


class TestSaveLoadEmbeddings:
    def test_roundtrip(self, tmp_path: Path):
        texts = ["hello world", "foo bar"]
        embeddings = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
        sha = "abc123"

        path = tmp_path / "test.npz"
        save_embeddings(path, texts, embeddings, sha)

        loaded_texts, loaded_emb, loaded_sha = load_embeddings(path)
        assert loaded_texts == texts
        np.testing.assert_array_almost_equal(loaded_emb, embeddings)
        assert loaded_sha == sha
