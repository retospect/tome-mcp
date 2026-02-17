"""Prompt injection scanner for PDF text.

Uses ProtectAI's DeBERTa-v3 model (ONNX) to detect prompt injection
attempts in extracted PDF text before ingestion into the vault.

The model is downloaded on first use (~180 MB) and cached by
``huggingface_hub`` in ``~/.cache/huggingface/``.

All runtime dependencies (``onnxruntime``, ``tokenizers``,
``huggingface_hub``, ``numpy``) are already transitive dependencies
of tome-mcp via chromadb / chonkie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_REPO = "ProtectAI/deberta-v3-base-prompt-injection-v2"
_ONNX_FILE = "onnx/model.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_MAX_LENGTH = 512
DEFAULT_THRESHOLD = 0.5
_MAX_BATCH_SIZE = 8  # cap to avoid OOM on large papers

# Lazy-loaded singletons
_session: Any = None
_tokenizer: Any = None


@dataclass
class ScanResult:
    """Result of scanning page texts for prompt injection."""

    flagged: bool
    max_score: float = 0.0
    flagged_pages: list[int] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax (1-D or 2-D)."""
    if x.ndim == 1:
        e = np.exp(x - np.max(x))
        return e / e.sum()
    # 2-D: softmax per row
    mx = np.max(x, axis=1, keepdims=True)
    e = np.exp(x - mx)
    return e / e.sum(axis=1, keepdims=True)


def _load_model() -> None:
    """Download (once) and cache the ONNX model + tokenizer."""
    global _session, _tokenizer  # noqa: PLW0603
    if _session is not None:
        return

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    model_path = hf_hub_download(MODEL_REPO, _ONNX_FILE)
    tokenizer_path = hf_hub_download(MODEL_REPO, _TOKENIZER_FILE)

    _session = ort.InferenceSession(
        model_path,
        providers=["CPUExecutionProvider"],
    )
    _tokenizer = Tokenizer.from_file(tokenizer_path)
    _tokenizer.enable_truncation(max_length=_MAX_LENGTH)


def _classify(text: str) -> float:
    """Return injection probability for a single text chunk."""
    scores = _classify_batch([text])
    return scores[0]


def _classify_batch(texts: list[str]) -> list[float]:
    """Return injection probabilities for a batch of texts."""
    _load_model()
    assert _session is not None and _tokenizer is not None

    encodings = _tokenizer.encode_batch(texts)
    max_len = max(len(enc.ids) for enc in encodings)

    # Pad to uniform length for batched inference
    batch_ids = np.zeros((len(texts), max_len), dtype=np.int64)
    batch_mask = np.zeros((len(texts), max_len), dtype=np.int64)
    for j, enc in enumerate(encodings):
        length = len(enc.ids)
        batch_ids[j, :length] = enc.ids
        batch_mask[j, :length] = enc.attention_mask

    input_names = {inp.name for inp in _session.get_inputs()}
    feeds: dict[str, np.ndarray] = {
        "input_ids": batch_ids,
        "attention_mask": batch_mask,
    }
    if "token_type_ids" in input_names:
        feeds["token_type_ids"] = np.zeros_like(batch_ids)

    logits = _session.run(None, feeds)[0]  # (batch, num_classes)
    probs = _softmax(logits)

    # Label 1 = INJECTION
    if probs.ndim == 1:
        return [float(probs[1]) if len(probs) > 1 else 0.0]
    return [float(row[1]) if len(row) > 1 else 0.0 for row in probs]


def scan_pages(
    page_texts: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> ScanResult:
    """Scan extracted page texts for prompt injection.

    Args:
        page_texts: Per-page text strings from PDF extraction.
        threshold: Score above which a page is flagged (0–1).

    Returns:
        ScanResult with per-page details.
    """
    if not page_texts:
        return ScanResult(flagged=False)

    try:
        _load_model()
    except Exception as e:
        logger.warning("Prompt injection scanner unavailable: %s", e)
        return ScanResult(flagged=False, error=str(e))

    # Filter to scannable pages (>= 20 chars)
    page_indices: list[int] = []  # 0-based indices into page_texts
    texts_to_scan: list[str] = []
    for i, text in enumerate(page_texts):
        if len(text.strip()) >= 20:
            page_indices.append(i)
            texts_to_scan.append(text)

    if not texts_to_scan:
        return ScanResult(flagged=False)

    # Process in bounded chunks to avoid OOM on large papers
    scores: list[float] = []
    try:
        for start in range(0, len(texts_to_scan), _MAX_BATCH_SIZE):
            chunk = texts_to_scan[start : start + _MAX_BATCH_SIZE]
            scores.extend(_classify_batch(chunk))
    except Exception as e:
        logger.warning("Batch scan failed: %s", e)
        return ScanResult(flagged=False, error=str(e))

    max_score = 0.0
    flagged_pages: list[int] = []
    details: list[dict[str, Any]] = []

    for idx, score in zip(page_indices, scores):
        page_num = idx + 1
        if score > max_score:
            max_score = score
        if score > threshold:
            flagged_pages.append(page_num)
            details.append({"page": page_num, "score": round(score, 4)})

    return ScanResult(
        flagged=len(flagged_pages) > 0,
        max_score=round(max_score, 4),
        flagged_pages=flagged_pages,
        details=details,
    )
