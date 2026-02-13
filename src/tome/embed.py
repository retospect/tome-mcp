"""Ollama embedding client.

Generates text embeddings via a local Ollama server for ChromaDB storage.
Handles batching and graceful degradation when Ollama is unavailable.
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import httpx
import numpy as np

from tome.errors import OllamaUnavailable

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
MAX_CHARS = 3500  # nomic-embed-text context window
BATCH_SIZE = 16
CONNECT_TIMEOUT = 5.0
REQUEST_TIMEOUT = 60.0


def get_ollama_url() -> str:
    """Get Ollama URL from environment or default."""
    return os.environ.get("TOME_OLLAMA_URL", DEFAULT_OLLAMA_URL)


def get_embed_model() -> str:
    """Get embedding model name from environment or default."""
    return os.environ.get("TOME_EMBED_MODEL", DEFAULT_MODEL)


def check_ollama(url: str | None = None) -> bool:
    """Check if Ollama is reachable.

    Args:
        url: Ollama server URL. Defaults to env/default.

    Returns:
        True if Ollama responds, False otherwise.
    """
    url = url or get_ollama_url()
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=CONNECT_TIMEOUT)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def embed_texts(
    texts: Sequence[str],
    url: str | None = None,
    model: str | None = None,
) -> np.ndarray:
    """Embed a batch of texts via Ollama.

    Args:
        texts: Texts to embed. Each is truncated to MAX_CHARS.
        url: Ollama server URL.
        model: Embedding model name.

    Returns:
        numpy array of shape (len(texts), embedding_dim).

    Raises:
        OllamaUnavailable: If Ollama cannot be reached.
    """
    url = url or get_ollama_url()
    model = model or get_embed_model()
    embed_url = f"{url}/api/embed"

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = [t[:MAX_CHARS] for t in texts[i : i + BATCH_SIZE]]
        try:
            resp = httpx.post(
                embed_url,
                json={"model": model, "input": batch},
                timeout=REQUEST_TIMEOUT,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise OllamaUnavailable(url) from e

        if resp.status_code != 200:
            raise OllamaUnavailable(url)

        data = resp.json()
        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(batch):
            raise OllamaUnavailable(url)

        all_embeddings.extend(embeddings)

    return np.array(all_embeddings, dtype=np.float32)


def embed_single(
    text: str,
    url: str | None = None,
    model: str | None = None,
) -> np.ndarray:
    """Embed a single text. Convenience wrapper.

    Returns:
        1D numpy array of the embedding vector.
    """
    result = embed_texts([text], url=url, model=model)
    return result[0]


def save_embeddings(
    path: str | os.PathLike,
    texts: list[str],
    embeddings: np.ndarray,
    source_sha256: str,
) -> None:
    """Save embeddings + texts to cache files.

    Embeddings are saved as .npz (no pickle). Texts and metadata
    are saved as a JSON sidecar (.json) to avoid unsafe pickle.

    Args:
        path: Output .npz path. A .json sidecar is created alongside.
        texts: The text chunks that were embedded.
        embeddings: The embedding matrix.
        source_sha256: SHA256 of the source file for invalidation.
    """
    np.savez_compressed(
        path,
        embeddings=embeddings,
    )
    # JSON sidecar for texts + metadata (no pickle needed)
    json_path = str(path).replace(".npz", ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"texts": texts, "source_sha256": source_sha256}, f, ensure_ascii=False)


def load_embeddings(path: str | os.PathLike) -> tuple[list[str], np.ndarray, str]:
    """Load embeddings from cache files.

    Returns:
        Tuple of (texts, embeddings, source_sha256).
    """
    data = np.load(path, allow_pickle=False)
    embeddings = data["embeddings"]

    json_path = str(path).replace(".npz", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    texts = meta["texts"]
    source_sha256 = meta["source_sha256"]
    return texts, embeddings, source_sha256
