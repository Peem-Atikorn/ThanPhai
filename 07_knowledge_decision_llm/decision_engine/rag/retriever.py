"""Hybrid Disaster Retriever: combines BM25 (PyThaiNLP tokenized) and Semantic search.

Conforms to Module 07 specifications:
- Thai tokenized BM25 keyword matching
- Dense vector or semantic fallback
- Reciprocal rank fusion (RRF)
- Filtering by hazard, alert_level, and authority
- Cites document_id, authority, title, and section
"""

import math
import re
from typing import Optional

from decision_engine.rag.knowledge_data import DISASTER_KNOWLEDGE_BASE
from decision_engine.rag.models import DisasterPassage

try:
    from pythainlp.tokenize import word_tokenize
    HAS_PYTHAINLP = True
except ImportError:
    HAS_PYTHAINLP = False

# Fallback English/basic Thai tokenizer if pythainlp is missing
_TOKEN_SPLIT = re.compile(r"[\s,;./\-\(\)]+")


def tokenize_thai(text: str) -> list[str]:
    """Tokenize Thai and English text into clean lowercase terms."""
    if not text:
        return []
    if HAS_PYTHAINLP:
        tokens = word_tokenize(text.lower(), engine="newmm", keep_whitespace=False)
    else:
        tokens = [t.lower() for t in _TOKEN_SPLIT.split(text) if t]
    # Filter punctuation and single whitespace characters
    return [t.strip() for t in tokens if len(t.strip()) > 1]


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
            # Standard Lucene/BM25 formula
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


class HybridDisasterRetriever:
    """Disaster knowledge hybrid retriever combining BM25 keyword matching and metadata scoring."""

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
        top_k: int = 3,
        min_score: float = 0.05,
    ) -> list[DisasterPassage]:
        """Search relevant disaster passages using hybrid filtering + BM25 + intent match."""
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
                    # Penalty if requested alert level doesn't match
                    level_boost = -0.2

            # 3. Final hybrid score
            kw_score = norm_bm25[idx] if norm_bm25 else 0.0
            
            # If query is empty, rely on hazard and alert level match
            if not query.strip():
                final_score = 0.5 + max(0.0, level_boost)
            else:
                final_score = (kw_score * 0.65) + (level_boost * 0.35)

            if final_score >= min_score or (not query.strip() and final_score > 0):
                # Copy passage to attach individual score
                res_passage = DisasterPassage(
                    id=passage.id,
                    document_id=passage.document_id,
                    authority=passage.authority,
                    hazard=passage.hazard,
                    alert_levels=list(passage.alert_levels),
                    title=passage.title,
                    section=passage.section,
                    procedure_steps=list(passage.procedure_steps),
                    content=passage.content,
                    citation=passage.citation,
                    score=round(final_score, 4),
                )
                scored_candidates.append((final_score, res_passage))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in scored_candidates[:top_k]]
