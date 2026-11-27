"""Lightweight local ONNX and deterministic fake text embeddings (zero auto-download)."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..config import Settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class TextEmbedder(Protocol):
    """Minimal synchronous embedding provider interface for indexing and retrieval."""

    model_id: str
    model_version: str
    dimensions: int

    def embed_texts(
        self, texts: Sequence[str], *, is_query: bool = False
    ) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        return [0.0] * len(vec)
    inv = 1.0 / norm
    return [v * inv for v in vec]


class DeterministicFakeEmbedder:
    """Zero-network deterministic 384-d hashed feature embedder for dev and automated tests."""

    def __init__(
        self,
        *,
        model_id: str = "fake-hash-embedder",
        model_version: str = "fake:1.0",
        dimensions: int = EMBEDDING_DIM,
    ) -> None:
        self.model_id = model_id
        self.model_version = model_version
        self.dimensions = dimensions

    def embed_texts(
        self, texts: Sequence[str], *, is_query: bool = False
    ) -> list[list[float]]:
        _ = is_query
        out: list[list[float]] = []
        dim = self.dimensions
        for text in texts:
            vec = [0.0] * dim
            tokens = _TOKEN_RE.findall(text.lower())
            features: list[tuple[str, float]] = [(tok, 1.5) for tok in tokens]
            for i in range(len(tokens) - 1):
                features.append((f"{tokens[i]}_{tokens[i + 1]}", 1.2))
            for tok in tokens:
                if len(tok) >= 4:
                    for j in range(len(tok) - 2):
                        features.append((f"ng:{tok[j : j + 3]}", 0.5))
            if not features:
                features.append(("__empty__", 1.0))
            for feat, weight in features:
                digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "little") % dim
                sign = 1.0 if (digest[4] & 1) == 0 else -1.0
                vec[idx] += sign * weight
            out.append(_l2_normalize(vec))
        return out


class _WordPieceTokenizer:
    """Pure-Python BERT/BGE WordPiece tokenizer over vocab.txt (zero transformers dependency)."""

    def __init__(self, vocab_path: Path, *, max_length: int = 512) -> None:
        vocab: dict[str, int] = {}
        with vocab_path.open("r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                vocab[line.rstrip("\r\n")] = idx
        self.vocab = vocab
        self.cls_id = vocab.get("[CLS]", 101)
        self.sep_id = vocab.get("[SEP]", 102)
        self.unk_id = vocab.get("[UNK]", 100)
        self.pad_id = vocab.get("[PAD]", 0)
        self.max_length = max_length

    @staticmethod
    def _clean_and_split(text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKD", text)
        stripped = "".join(
            ch for ch in normalized if unicodedata.category(ch) != "Mn"
        ).lower()
        tokens: list[str] = []
        buf: list[str] = []
        for ch in stripped:
            cat = unicodedata.category(ch)
            is_punct = cat.startswith(("P", "S"))
            if ch.isspace() or is_punct:
                if buf:
                    tokens.append("".join(buf))
                    buf.clear()
                if is_punct:
                    tokens.append(ch)
            else:
                buf.append(ch)
        if buf:
            tokens.append("".join(buf))
        return tokens

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self.unk_id]
        sub_ids: list[int] = []
        start = 0
        while start < len(word):
            end = len(word)
            matched: int | None = None
            while start < end:
                sub = word[start:end]
                if start > 0:
                    sub = "##" + sub
                tid = self.vocab.get(sub)
                if tid is not None:
                    matched = tid
                    break
                end -= 1
            if matched is None:
                return [self.unk_id]
            sub_ids.append(matched)
            start = end
        return sub_ids

    def encode_batch(self, texts: Sequence[str]) -> tuple[list[list[int]], list[list[int]]]:
        encoded: list[list[int]] = []
        max_len = 0
        limit = self.max_length - 2
        for text in texts:
            ids: list[int] = [self.cls_id]
            for word in self._clean_and_split(text):
                ids.extend(self._wordpiece(word))
                if len(ids) - 1 >= limit:
                    ids = ids[: limit + 1]
                    break
            ids.append(self.sep_id)
            encoded.append(ids)
            max_len = max(max_len, len(ids))
        padded_ids: list[list[int]] = []
        masks: list[list[int]] = []
        for ids in encoded:
            pad_len = max_len - len(ids)
            padded_ids.append(ids + [self.pad_id] * pad_len)
            masks.append([1] * len(ids) + [0] * pad_len)
        return padded_ids, masks


class LocalOnnxEmbedder:
    """Lightweight CPU ONNX Runtime embedder (e.g. BAAI/bge-small-en-v1.5 INT8, 384-d)."""

    def __init__(
        self,
        model_dir: Path | str,
        *,
        model_id: str = "BAAI/bge-small-en-v1.5",
        model_version: str = "int8-onnx:1.0",
        dimensions: int = EMBEDDING_DIM,
        intra_op_num_threads: int = 4,
    ) -> None:
        base = Path(model_dir)
        onnx_path = base / "model_quantized.onnx"
        if not onnx_path.is_file():
            onnx_path = base / "model.onnx"
        vocab_path = base / "vocab.txt"
        if not onnx_path.is_file() or not vocab_path.is_file():
            raise FileNotFoundError(
                f"Local ONNX embedding artifacts missing in {base}; expected model_quantized.onnx (or model.onnx) and vocab.txt"
            )
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, intra_op_num_threads)
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {inp.name for inp in self._session.get_inputs()}
        self._tokenizer = _WordPieceTokenizer(vocab_path)
        self.model_id = model_id
        self.model_version = model_version
        self.dimensions = dimensions

    def embed_texts(
        self, texts: Sequence[str], *, is_query: bool = False
    ) -> list[list[float]]:
        if not texts:
            return []
        import numpy as np

        prepared = [
            (_BGE_QUERY_PREFIX + t) if is_query else t
            for t in texts
        ]
        input_ids, attention_mask = self._tokenizer.encode_batch(prepared)
        ids_arr = np.asarray(input_ids, dtype=np.int64)
        mask_arr = np.asarray(attention_mask, dtype=np.int64)
        feeds: dict[str, object] = {
            "input_ids": ids_arr,
            "attention_mask": mask_arr,
        }
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids_arr, dtype=np.int64)
        outputs = self._session.run(None, feeds)
        token_embeddings = outputs[0]
        # BGE-small-en-v1.5 uses CLS pooling + L2 normalization
        cls_vecs = token_embeddings[:, 0, : self.dimensions].astype(np.float32)
        norms = np.linalg.norm(cls_vecs, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        normalized = cls_vecs / norms
        return [row.tolist() for row in normalized]


def _resolve_local_onnx_dir(raw_dir: str | None) -> Path | None:
    if not raw_dir:
        return None
    raw_path = Path(raw_dir)
    candidates = [raw_path]
    if not raw_path.is_absolute():
        candidates.append(Path(__file__).resolve().parents[3] / raw_path)
    for base in candidates:
        has_model = (base / "model_quantized.onnx").is_file() or (base / "model.onnx").is_file()
        if has_model and (base / "vocab.txt").is_file():
            return base
    return None


def build_embedder_from_settings(settings: Settings) -> TextEmbedder | None:
    """Construct configured text embedder; never falls back to fake outside test."""
    if settings.embedding_provider == "fake":
        if settings.environment != "test":
            raise ValueError("Fake embedding provider is restricted to test environments")
        return DeterministicFakeEmbedder()
    if settings.embedding_provider == "local_onnx":
        model_dir = _resolve_local_onnx_dir(settings.local_embedding_model_dir)
        if model_dir is None:
            logger.warning(
                "Local ONNX embedding artifacts unavailable at %r; retrieval will use lexical fallback",
                settings.local_embedding_model_dir,
            )
            return None
        return LocalOnnxEmbedder(model_dir)
    return None
