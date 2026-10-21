"""Disaster Knowledge Retriever: BM25 keyword matching with PyThaiNLP tokenization and alert level prioritization."""

import math
import re
from dataclasses import replace
from typing import Optional

from pythainlp.corpus import thai_stopwords
from pythainlp.tokenize import word_tokenize

from decision_engine.rag.knowledge_data import DISASTER_KNOWLEDGE_BASE
from decision_engine.rag.models import DisasterPassage

THAI_STOPWORDS = set(thai_stopwords())


def normalize_thai(text: str) -> str:
    """Normalize Thai text including decomposed vowels (e.g. sara-am) and PDF artifacts."""
    if not text:
        return ""
    # Strip invisible zero-width chars and soft hyphens
    text = text.replace("\u200b", "").replace("\xad", "")
    # Fix replacement characters and decomposed vowel patterns
    text = text.replace("\ufffd้ำ", "้ำ").replace("\ufffdำ", "ำ").replace("\ufffd้า", "้ำ").replace("\ufffdา", "ำ")
    text = text.replace("นำ้", "น้ำ").replace("น้้า", "น้ำ")
    text = re.sub(r"([\u0e48-\u0e4b])\u0e4d\u0e32", r"\1ำ", text)
    text = re.sub(r"\u0e4d([\u0e48-\u0e4b])\u0e32", r"\1ำ", text)
    text = text.replace("\u0e4d\u0e32", "ำ")
    text = re.sub(r"ำ([\u0e48-\u0e4b])", r"\1ำ", text)
    return text


def tokenize_thai(text: str) -> list[str]:
    """Tokenize Thai and English text into clean lowercase terms using PyThaiNLP."""
    if not text:
        return []
    clean_text = normalize_thai(text.lower())
    tokens = word_tokenize(clean_text, engine="newmm", keep_whitespace=False)
    # Filter stopwords, punctuation, and single characters
    return [t.strip() for t in tokens if len(t.strip()) > 1 and t.strip() not in THAI_STOPWORDS]


class BM25Index:
    """Lightweight in-memory BM25 index with PyThaiNLP tokenization."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus: list[DisasterPassage] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_lens: list[int] = []
        self.avgdl: float = 0.0
        self.df: dict[str, int] = {}
        self.idf: dict[str, float] = {}

    def index(self, passages: list[DisasterPassage]) -> None:
        self.corpus = passages
        self.doc_tokens = [tokenize_thai(p.full_text()) for p in passages]
        self.doc_lens = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_lens) / len(self.doc_lens) if self.doc_lens else 1.0

        # Calculate document frequency
        self.df = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

        # Calculate IDF
        N = len(self.corpus)
        self.idf = {}
        for term, freq in self.df.items():
            self.idf[term] = math.log(1.0 + (N - freq + 0.5) / (freq + 0.5))

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * len(self.corpus)
        if not query_tokens or not self.corpus:
            return scores

        for term in query_tokens:
            if term not in self.idf:
                continue
            idf_val = self.idf[term]
            for idx, doc in enumerate(self.doc_tokens):
                tf = doc.count(term)
                if tf == 0:
                    continue
                num = tf * (self.k1 + 1.0)
                denom = tf + self.k1 * (1.0 - self.b + self.b * (self.doc_lens[idx] / self.avgdl))
                scores[idx] += idf_val * (num / denom)
        return scores


class DisasterRetriever:
    """Disaster knowledge retriever using BM25 keyword matching with alert level relevance boost."""

    def __init__(self, passages: Optional[list[DisasterPassage]] = None):
        self.passages = passages if passages is not None else list(DISASTER_KNOWLEDGE_BASE)
        self.bm25 = BM25Index()
        self.bm25.index(self.passages)

    def search(
        self,
        query: str,
        hazard: Optional[str] = None,
        alert_level: Optional[str] = None,
        authority: Optional[str] = None,
        top_k: int = 2,
        min_score: float = 0.05,
    ) -> list[DisasterPassage]:
        """Search relevant disaster passages using BM25 and alert level filtering."""
        query_tokens = tokenize_thai(query)
        bm25_scores = self.bm25.score(query_tokens)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores) if bm25_scores and max(bm25_scores) > 0 else 1.0
        norm_bm25 = [s / max_bm25 for s in bm25_scores]

        scored_candidates: list[tuple[float, DisasterPassage]] = []

        for idx, passage in enumerate(self.passages):
            # 1. Hard filters
            if hazard and passage.hazard.lower() != hazard.lower():
                continue
            if authority and passage.authority.lower() != authority.lower():
                continue

            # 2. Level relevance
            level_boost = 0.0
            if alert_level:
                if alert_level.upper() in [lvl.upper() for lvl in passage.alert_levels]:
                    level_boost = 0.35
                else:
                    level_boost = -0.2

            # 3. Final score
            kw_score = norm_bm25[idx] if norm_bm25 else 0.0
            if not query.strip():
                final_score = 0.5 + max(0.0, level_boost)
            else:
                final_score = (kw_score * 0.65) + (level_boost * 0.35)

            if final_score >= min_score or (not query.strip() and final_score > 0):
                scored_candidates.append((final_score, replace(passage, score=round(final_score, 4))))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in scored_candidates[:top_k]]


# Backward compatibility alias
HybridDisasterRetriever = DisasterRetriever
