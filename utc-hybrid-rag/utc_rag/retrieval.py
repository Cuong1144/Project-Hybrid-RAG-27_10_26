import re
import math
import os
import threading
import time
import unicodedata
import numpy as np
from rank_bm25 import BM25Okapi

_models = {}
_model_lock = threading.Lock()


def make_bm25(corpus):
    model = BM25Okapi(corpus)
    # Positive Robertson IDF preserves matching terms in very small pilot corpora.
    frequencies = {}
    for document in corpus:
        for term in set(document):
            frequencies[term] = frequencies.get(term, 0) + 1
    model.idf = {term: math.log(1 + (len(corpus) - count + 0.5) / (count + 0.5))
                 for term, count in frequencies.items()}
    return model


def tokenize(text):
    text = unicodedata.normalize("NFC", text).casefold()
    return re.findall(r"[^\W_]+(?:[./-][^\W_]+)*", text, re.UNICODE)


class Embedder:
    def __init__(self, contract, cache_dir):
        import torch
        torch.set_num_threads(int(os.getenv("UTC_EMBEDDING_THREADS", "4")))
        from sentence_transformers import SentenceTransformer
        self.contract = contract
        key = (contract["model"], contract["revision"], str(cache_dir))
        with _model_lock:
            if key not in _models:
                _models[key] = SentenceTransformer(contract["model"], revision=contract["revision"],
                                                   cache_folder=str(cache_dir), device="cpu", local_files_only=True)
            self.model = _models[key]

    @staticmethod
    def download(model, revision, cache_dir):
        from huggingface_hub import HfApi, snapshot_download
        sha = HfApi().model_info(model, revision=revision).sha
        snapshot_download(model, revision=sha, cache_dir=str(cache_dir),
                          ignore_patterns=["onnx/*", "openvino/*", "*.h5", "*.ot", "pytorch_model.bin"])
        return {"model": model, "revision": sha, "tokenizer_revision": sha, "pooling": "mean",
                "query_instruction": "query: ", "document_instruction": "passage: ",
                "dimension": 384, "metric": "inner_product", "normalize": True,
                "device": "cpu", "max_tokens": 512}

    def encode(self, texts, query=False):
        prefix = self.contract["query_instruction" if query else "document_instruction"]
        inputs = [prefix + text for text in texts]
        if any(len(self.model.tokenizer.encode(value)) > self.model.max_seq_length for value in inputs):
            raise ValueError("Nội dung vượt giới hạn tokenizer embedding; cần giảm chunk hoặc rút gọn câu hỏi.")
        vectors = self.model.encode(inputs, batch_size=16, normalize_embeddings=True, show_progress_bar=len(inputs) > 32)
        vectors = np.asarray(vectors, dtype="float32")
        if vectors.ndim != 2 or vectors.shape[1] != self.contract["dimension"] or not np.isfinite(vectors).all():
            raise ValueError("Vector embedding không hợp lệ.")
        return vectors


def rrf(branches, k=60):
    combined = {}
    for name, hits in branches.items():
        seen = set()
        for rank, hit in enumerate(hits, 1):
            cid = hit["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            value = combined.setdefault(cid, {"chunk_id": cid, "rrf_score": 0.0,
                                               "bm25_rank": None, "faiss_rank": None})
            value["rrf_score"] += 1 / (k + rank)
            value[name + "_rank"] = rank
            value[name + "_score"] = float(hit["score"])
    return sorted(combined.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"]))


def metadata_value(metadata, key):
    value = metadata.get(key)
    if isinstance(value, dict) and value.get("verification_status") == "verified" and value.get("evidence_ids"):
        return value.get("value")
    return None


class RetrievalIncomplete(Exception):
    def __init__(self, phase, partial):
        self.phase, self.partial = phase, partial
        super().__init__("Retrieval deadline exceeded before " + phase)


def retrieve(bundle, query, filters=None, top_k=16, timeout=15.0):
    started = time.perf_counter()
    filters = filters or {}
    eligible = [i for i, chunk in enumerate(bundle.chunks)
                if all(str(metadata_value(chunk.get("metadata", {}), key)) == str(value)
                       for key, value in filters.items() if value is not None)]
    if not eligible:
        return {"results": [], "bm25": [], "faiss": [], "eligible_count": 0}
    # Both branches receive exactly the same mask before selecting their Top 8.
    bm25_scores = bundle.bm25.get_scores(tokenize(query))
    lexical = sorted((i for i in eligible if bm25_scores[i] > 0), key=lambda i: (-bm25_scores[i], bundle.chunks[i]["chunk_id"]))[:8]
    bm25 = [{"chunk_id": bundle.chunks[i]["chunk_id"], "score": float(bm25_scores[i])} for i in lexical]
    if time.perf_counter() - started >= timeout:
        raise RetrievalIncomplete("embedding", {"bm25": bm25, "eligible_count": len(eligible)})
    vector = bundle.embedder.encode([query], query=True)
    if time.perf_counter() - started >= timeout:
        raise RetrievalIncomplete("faiss", {"bm25": bm25, "eligible_count": len(eligible)})
    # Exact FAISS subset search avoids dropping narrow scopes after a global Top8.
    import faiss
    subset = faiss.IndexFlatIP(bundle.vectors.shape[1])
    subset.add(np.ascontiguousarray(bundle.vectors[eligible]))
    distances, positions = subset.search(vector, min(8, len(eligible)))
    semantic = [{"chunk_id": bundle.chunks[eligible[int(pos)]]["chunk_id"], "score": float(score)}
                for score, pos in zip(distances[0], positions[0]) if pos >= 0]
    fused = rrf({"bm25": bm25, "faiss": semantic})
    by_id = {chunk["chunk_id"]: chunk for chunk in bundle.chunks}
    results = [{**by_id[item["chunk_id"]], **item} for item in fused[:top_k]]
    return {"results": results, "bm25": bm25, "faiss": semantic, "eligible_count": len(eligible)}
