"""
db/vector_store.py — 向量搜索引擎

Provides semantic search over novel content, facts, and character states using
ChromaDB + local Chinese embedding model (BAAI/bge-small-zh-v1.5).

KEY DESIGN:
  - ChromaDB runs embedded (in-process), no separate server needed.
  - Data persists to disk under data/_vector_db/<novel_id>/collections/.
  - Graceful fallback: if model/chromadb is unavailable, falls back to
    keyword-based or recent-fact search without crashing.
  - Each novel gets its own ChromaDB collection for isolation.
  - env VAR VECTOR_SEARCH_ENABLED=false to force-disable.

COLLECTIONS:
  facts       — chapter facts (fact_type, fact_text, keywords, chapter_no)
  characters  — character state snapshots
  summaries   — chapter summaries for cross-chapter context
  rules       — world rules for consistency checking
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── embedding model (lazy loaded) ──────────────────────────────
_EMBEDDER = None  # singleton
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_EMBEDDING_DIM = 512

# ── ChromaDB (lazy initialized) ─────────────────────────────────
_CHROMA_CLIENT = None
_CHROMA_ENABLED = None  # None = not checked yet


def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    try:
        import os as _os
        from config import get_settings
        _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        _os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
        cuda_dev = get_settings().embedding_cuda_device
        if cuda_dev:
            _os.environ.setdefault("CUDA_VISIBLE_DEVICES", cuda_dev)
        from sentence_transformers import SentenceTransformer
        _EMBEDDER = SentenceTransformer(_MODEL_NAME)
        logger.info(f"[VectorStore] Embedding model loaded: {_MODEL_NAME} ({_EMBEDDING_DIM}d)")
        return _EMBEDDER
    except Exception as e:
        logger.warning(f"[VectorStore] Embedding model FAILED: {e}")
        return None


def _get_chroma():
    global _CHROMA_CLIENT, _CHROMA_ENABLED
    if _CHROMA_ENABLED is False:
        return None
    if _CHROMA_CLIENT is not None:
        return _CHROMA_CLIENT

    # Check env toggle
    if os.environ.get("VECTOR_SEARCH_ENABLED", "true").lower() == "false":
        _CHROMA_ENABLED = False
        logger.info("[VectorStore] Disabled by VECTOR_SEARCH_ENABLED=false")
        return None

    try:
        import chromadb
        from chromadb.config import Settings
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "_vector_db"
        )
        os.makedirs(data_dir, exist_ok=True)
        _CHROMA_CLIENT = chromadb.PersistentClient(
            path=data_dir,
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        _CHROMA_ENABLED = True
        logger.info(f"[VectorStore] ChromaDB ready: {data_dir}")
        return _CHROMA_CLIENT
    except Exception as e:
        logger.warning(f"[VectorStore] ChromaDB FAILED, falling back to keyword search: {e}")
        _CHROMA_ENABLED = False
        return None


# ── Public API ──────────────────────────────────────────────────


def is_available() -> bool:
    """Check if vector search is fully operational (chromadb + model)."""
    client = _get_chroma()
    embedder = _get_embedder()
    return client is not None and embedder is not None


def add_facts(novel_id: str, facts: list[dict]) -> int:
    """Add facts to the vector store.
    
    facts: list of {fact_type, fact_text, keywords, chapter_no}
    Returns count of facts actually indexed.
    """
    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder:
        return 0  # silently skip

    try:
        col_name = f"{novel_id}_facts"
        try:
            col = client.get_collection(col_name)
        except Exception:
            col = client.create_collection(
                name=col_name,
                metadata={"hnsw:space": "cosine"},
            )

        texts = []
        metadatas = []
        ids = []
        for f in facts:
            text = f.get("fact_text", "").strip()
            ft = f.get("fact_type", "other")
            cn = f.get("chapter_no", 0)
            kw = f.get("keywords", "")
            if not text:
                continue

            # Composite text for rich semantic search
            search_text = f"{ft}: {text}"
            if kw:
                search_text += f" ({kw})"

            doc_id = f"{novel_id}_{ft}_{cn}_{abs(hash(text))}"
            texts.append(search_text)
            metadatas.append({
                "novel_id": novel_id,
                "fact_type": ft,
                "chapter_no": cn,
                "keywords": kw[:200],
                "fact_text": text[:500],
            })
            ids.append(doc_id)

        if not texts:
            return 0

        # Upsert so rewrite_volume() can re-index without duplicate-ID errors
        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        col.upsert(
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids,
        )
        logger.debug(f"[VectorStore] Indexed {len(texts)} facts for {novel_id}")
        return len(texts)
    except Exception as e:
        logger.warning(f"[VectorStore] add_facts failed: {e}")
        return 0


def search_facts(
    novel_id: str,
    query: str,
    n_results: int = 10,
    fact_type: Optional[str] = None,
    max_chapter: Optional[int] = None,
    min_chapter: Optional[int] = None,
) -> list[dict]:
    """Semantic search over novel facts.
    
    Returns list of {fact_type, fact_text, keywords, chapter_no, score}
    Falls back to keyword search when vector DB unavailable.
    """
    if not query:
        return []

    client = _get_chroma()
    embedder = _get_embedder()
    if client and embedder:
        try:
            col_name = f"{novel_id}_facts"
            try:
                col = client.get_collection(col_name)
            except Exception:
                col = None

            if col:
                # Build where filter
                # ChromaDB 1.5.x requires $and/$or for multi-field filters
                where_conditions = [{"novel_id": novel_id}]
                if fact_type:
                    where_conditions.append({"fact_type": fact_type})
                if max_chapter is not None:
                    where_conditions.append({"chapter_no": {"$lte": max_chapter}})
                if min_chapter is not None:
                    where_conditions.append({"chapter_no": {"$gte": min_chapter}})
                where = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]

                query_emb = embedder.encode([query]).tolist()
                results = col.query(
                    query_embeddings=query_emb,
                    n_results=n_results * 2,  # fetch more, filter below
                    where=where,
                )

                hits = []
                if results and results.get("metadatas"):
                    for i in range(len(results["metadatas"][0])):
                        meta = results["metadatas"][0][i]
                        score = results["distances"][0][i] if results.get("distances") else 0.0
                        hits.append({
                            "fact_type": meta.get("fact_type", "other"),
                            "fact_text": meta.get("fact_text", ""),
                            "keywords": meta.get("keywords", ""),
                            "chapter_no": meta.get("chapter_no", 0),
                            "score": round(1.0 - score, 3),  # cosine → similarity
                        })
                    # Deduplicate by fact_text, keep highest score
                    seen = set()
                    deduped = []
                    for h in sorted(hits, key=lambda x: -x["score"]):
                        if h["fact_text"] not in seen:
                            seen.add(h["fact_text"])
                            deduped.append(h)

                    # Hybrid rerank: 70% cosine + 30% Chinese character recall
                    query_chars = set(c for c in query if '一' <= c <= '鿿')
                    if query_chars:
                        for h in deduped:
                            text = h["fact_text"] + " " + h["keywords"]
                            hits_n = sum(1 for c in query_chars if c in text)
                            kw_score = hits_n / len(query_chars)
                            h["score"] = round(0.7 * h["score"] + 0.3 * kw_score, 3)
                        deduped.sort(key=lambda x: -x["score"])

                    return deduped[:n_results]
        except Exception as e:
            logger.debug(f"[VectorStore] search_facts vector failed, fallback: {e}")

    # ── Fallback: keyword search ─────────────────────────
    return _fallback_keyword_search(novel_id, query, n_results, fact_type)


def search_summaries(
    novel_id: str,
    query: str,
    n_results: int = 5,
) -> list[dict]:
    """Semantic search over chapter summaries."""
    client = _get_chroma()
    embedder = _get_embedder()
    if client and embedder:
        try:
            col_name = f"{novel_id}_summaries"
            try:
                col = client.get_collection(col_name)
            except Exception:
                return []

            query_emb = embedder.encode([query]).tolist()
            results = col.query(
                query_embeddings=query_emb,
                n_results=n_results,
                where={"novel_id": novel_id},
            )

            hits = []
            if results and results.get("metadatas"):
                for i in range(len(results["metadatas"][0])):
                    meta = results["metadatas"][0][i]
                    score = results["distances"][0][i] if results.get("distances") else 0.0
                    hits.append({
                        "chapter_no": meta.get("chapter_no", 0),
                        "summary_text": meta.get("summary_text", ""),
                        "score": round(1.0 - score, 3),
                    })
                return hits[:n_results]
        except Exception:
            pass
    return []


def add_summaries(novel_id: str, summaries: list[dict]) -> int:
    """Index chapter summaries for semantic search."""
    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder or not summaries:
        return 0

    try:
        col_name = f"{novel_id}_summaries"
        try:
            col = client.get_collection(col_name)
        except Exception:
            col = client.create_collection(
                name=col_name,
                metadata={"hnsw:space": "cosine"},
            )

        texts = []
        metadatas = []
        ids = []
        for s in summaries:
            text = s.get("summary_text", "").strip()
            cn = s.get("chapter_no", 0)
            if not text:
                continue
            doc_id = f"{novel_id}_summary_{cn}"
            texts.append(text)
            metadatas.append({
                "novel_id": novel_id,
                "chapter_no": cn,
                "summary_text": text[:500],
            })
            ids.append(doc_id)

        if not texts:
            return 0
        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        col.upsert(embeddings=embeddings, documents=texts, metadatas=metadatas, ids=ids)
        logger.info(f"[VectorStore] Indexed {len(texts)} summaries for {novel_id}")
        return len(texts)
    except Exception as e:
        logger.warning(f"[VectorStore] add_summaries failed: {e}")
        return 0


def add_characters(novel_id: str, characters: list[dict]) -> int:
    """Index character state snapshots for semantic search.

    characters: list of dicts with char_id, name, power_level, location,
                status, background, personality fields.
    Uses upsert so re-indexing an existing character updates it.
    Returns count of characters actually indexed.
    """
    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder:
        return 0

    try:
        col_name = f"{novel_id}_characters"
        try:
            col = client.get_collection(col_name)
        except Exception:
            col = client.create_collection(
                name=col_name,
                metadata={"hnsw:space": "cosine"},
            )

        texts = []
        metadatas = []
        ids = []
        for c in characters:
            char_id = c.get("char_id", "")
            name = c.get("name", char_id)
            if not char_id and not name:
                continue
            background = c.get("background", c.get("backstory", "")) or ""
            personality = c.get("personality", "")
            if isinstance(personality, list):
                personality = " ".join(personality)
            power_level = c.get("power_level", "") or ""
            search_text = f"{name}: {background} {personality} {power_level}".strip()

            doc_id = f"{novel_id}_char_{char_id or name}"
            texts.append(search_text)
            metadatas.append({
                "novel_id": novel_id[:200],
                "char_id": str(char_id)[:200],
                "name": str(name)[:200],
                "power_level": str(power_level)[:200],
            })
            ids.append(doc_id)

        if not texts:
            return 0

        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        col.upsert(
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids,
        )
        logger.debug(f"[VectorStore] Upserted {len(texts)} characters for {novel_id}")
        return len(texts)
    except Exception as e:
        logger.warning(f"[VectorStore] add_characters failed: {e}")
        return 0


def search_characters(
    novel_id: str,
    query: str,
    n_results: int = 5,
) -> list[dict]:
    """Semantic search over character state snapshots.

    Returns list of {char_id, name, power_level, score}.
    Falls back to empty list (no keyword fallback needed for characters).
    """
    if not query:
        return []

    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder:
        return []

    try:
        col_name = f"{novel_id}_characters"
        try:
            col = client.get_collection(col_name)
        except Exception:
            return []

        query_emb = embedder.encode([query]).tolist()
        results = col.query(
            query_embeddings=query_emb,
            n_results=n_results,
            where={"novel_id": novel_id},
        )

        hits = []
        if results and results.get("metadatas"):
            for i in range(len(results["metadatas"][0])):
                meta = results["metadatas"][0][i]
                score = results["distances"][0][i] if results.get("distances") else 0.0
                hits.append({
                    "char_id": meta.get("char_id", ""),
                    "name": meta.get("name", ""),
                    "power_level": meta.get("power_level", ""),
                    "score": round(1.0 - score, 3),
                })
        return hits[:n_results]
    except Exception as e:
        logger.debug(f"[VectorStore] search_characters failed: {e}")
        return []


def add_rules(novel_id: str, rules: list[dict]) -> int:
    """Index world rules for semantic search.

    rules: list of dicts with rule_type, rule_text, source (optional).
    Uses upsert so re-indexing a rule updates it.
    Returns count of rules actually indexed.
    """
    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder:
        return 0

    try:
        col_name = f"{novel_id}_rules"
        try:
            col = client.get_collection(col_name)
        except Exception:
            col = client.create_collection(
                name=col_name,
                metadata={"hnsw:space": "cosine"},
            )

        texts = []
        metadatas = []
        ids = []
        for r in rules:
            rule_type = r.get("rule_type", "general")
            rule_text = r.get("rule_text", "").strip()
            if not rule_text:
                continue
            search_text = f"{rule_type}: {rule_text}"
            doc_id = f"{novel_id}_rule_{abs(hash(rule_text))}"
            texts.append(search_text)
            metadatas.append({
                "novel_id": novel_id[:200],
                "rule_type": str(rule_type)[:200],
                "rule_text": rule_text[:500],
            })
            ids.append(doc_id)

        if not texts:
            return 0

        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        col.upsert(
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids,
        )
        logger.debug(f"[VectorStore] Upserted {len(texts)} rules for {novel_id}")
        return len(texts)
    except Exception as e:
        logger.warning(f"[VectorStore] add_rules failed: {e}")
        return 0


def search_rules(
    novel_id: str,
    query: str,
    n_results: int = 5,
) -> list[dict]:
    """Semantic search over world rules.

    Returns list of {rule_type, rule_text, score}.
    """
    if not query:
        return []

    client = _get_chroma()
    embedder = _get_embedder()
    if not client or not embedder:
        return []

    try:
        col_name = f"{novel_id}_rules"
        try:
            col = client.get_collection(col_name)
        except Exception:
            return []

        query_emb = embedder.encode([query]).tolist()
        results = col.query(
            query_embeddings=query_emb,
            n_results=n_results,
            where={"novel_id": novel_id},
        )

        hits = []
        if results and results.get("metadatas"):
            for i in range(len(results["metadatas"][0])):
                meta = results["metadatas"][0][i]
                score = results["distances"][0][i] if results.get("distances") else 0.0
                hits.append({
                    "rule_type": meta.get("rule_type", ""),
                    "rule_text": meta.get("rule_text", ""),
                    "score": round(1.0 - score, 3),
                })
        return hits[:n_results]
    except Exception as e:
        logger.debug(f"[VectorStore] search_rules failed: {e}")
        return []


# ── Delete & clean ──────────────────────────────────────────────


def delete_novel(novel_id: str) -> bool:
    """Delete all vector data for a novel."""
    client = _get_chroma()
    if not client:
        return False
    try:
        for suffix in ["_facts", "_summaries", "_characters", "_rules"]:
            col_name = f"{novel_id}{suffix}"
            try:
                client.delete_collection(col_name)
            except Exception:
                pass
        logger.info(f"[VectorStore] Deleted all data for {novel_id}")
        return True
    except Exception as e:
        logger.warning(f"[VectorStore] delete_novel failed: {e}")
        return False


def delete_facts_by_chapter_range(novel_id: str, chapter_start: int, chapter_end: int) -> int:
    """Delete all facts for chapters in [chapter_start, chapter_end] inclusive."""
    client = _get_chroma()
    if not client:
        return 0
    try:
        col_name = f"{novel_id}_facts"
        try:
            col = client.get_collection(col_name)
        except Exception:
            return 0
        where = {"$and": [
            {"novel_id": novel_id},
            {"chapter_no": {"$gte": chapter_start}},
            {"chapter_no": {"$lte": chapter_end}},
        ]}
        result = col.get(where=where)
        ids = result.get("ids", [])
        if ids:
            col.delete(ids=ids)
        logger.info(f"[VectorStore] Deleted {len(ids)} facts for {novel_id} ch{chapter_start}-{chapter_end}")
        return len(ids)
    except Exception as e:
        logger.warning(f"[VectorStore] delete_facts_by_chapter_range failed: {e}")
        return 0


def delete_summaries_by_chapter_range(novel_id: str, chapter_start: int, chapter_end: int) -> int:
    """Delete all summaries for chapters in [chapter_start, chapter_end] inclusive."""
    client = _get_chroma()
    if not client:
        return 0
    try:
        col_name = f"{novel_id}_summaries"
        try:
            col = client.get_collection(col_name)
        except Exception:
            return 0
        where = {"$and": [
            {"novel_id": novel_id},
            {"chapter_no": {"$gte": chapter_start}},
            {"chapter_no": {"$lte": chapter_end}},
        ]}
        result = col.get(where=where)
        ids = result.get("ids", [])
        if ids:
            col.delete(ids=ids)
        logger.info(f"[VectorStore] Deleted {len(ids)} summaries for {novel_id} ch{chapter_start}-{chapter_end}")
        return len(ids)
    except Exception as e:
        logger.warning(f"[VectorStore] delete_summaries_by_chapter_range failed: {e}")
        return 0


# ── Fallback ────────────────────────────────────────────────────


def _fallback_keyword_search(
    novel_id: str,
    query: str,
    n_results: int = 10,
    fact_type: Optional[str] = None,
) -> list[dict]:
    """Simple keyword-based fallback when vector DB is unavailable."""
    try:
        from db import repo as _repo

        # Get recent facts
        all_facts = _repo.get_recent_facts(novel_id, 0, limit=200)
        if not all_facts:
            return []

        keywords = query.lower().split()
        scored = []
        for f in all_facts:
            text = (f.get("fact_text", "") + " " + f.get("keywords", "")).lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scored.append((score, f))

        scored.sort(key=lambda x: -x[0])
        results = []
        for score, f in scored[:n_results]:
            results.append({
                "fact_type": f.get("fact_type", "other"),
                "fact_text": f.get("fact_text", ""),
                "keywords": f.get("keywords", ""),
                "chapter_no": f.get("chapter_no", 0),
                "score": round(score / len(keywords), 2) if keywords else 0,
            })
        return results
    except Exception:
        return []


# ── Init hook (called by app startup) ──────────────────────────


def init_vector_store():
    """Warm up the vector store on startup. Logs status."""
    if _get_chroma() and _get_embedder():
        logger.info(
            "[VectorStore] ✅ 向量搜索引擎已就绪"
            f"  (model={_MODEL_NAME}, dim={_EMBEDDING_DIM})"
        )
        return True
    logger.info(
        "[VectorStore] ⚠️ 向量搜索引擎不可用，使用关键词兜底模式"
    )
    return False
