"""
engine/chitta.py — Chitta: Vector Retrieval (Qdrant Cloud)
Anbu Health AI — Antahkarana Pipeline v2.0
Falls back to in-memory if Qdrant unavailable.
"""
import os
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

class Chitta:
    def __init__(self):
        self._client = None
        self._fallback_mode = False
        self._setup()

    def _setup(self):
        qdrant_url = os.environ.get("QDRANT_URL", "")
        qdrant_key = os.environ.get("QDRANT_API_KEY", "")

        if qdrant_url and qdrant_key:
            try:
                from qdrant_client import QdrantClient
                self._client = QdrantClient(url=qdrant_url, api_key=qdrant_key)
                logger.info("[CHITTA] Qdrant Cloud connected")
                return
            except Exception as e:
                logger.warning(f"[CHITTA] Qdrant failed: {e}")

        # Fallback: ChromaDB
        try:
            import chromadb
            self._chroma = chromadb.PersistentClient(path="/tmp/anbu_chroma")
            self._fallback_mode = True
            logger.info("[CHITTA] ChromaDB fallback active")
        except Exception as e:
            logger.warning(f"[CHITTA] ChromaDB fallback failed: {e}")
            self._fallback_mode = None  # no vector DB

    def retrieve(self, question: str, entities: List[str], k: int = 5) -> Dict:
        query = question + " " + " ".join(entities[:3])

        if self._client:
            return self._qdrant_retrieve(query, k)
        elif self._fallback_mode:
            return self._chroma_retrieve(query, k)
        else:
            return self._empty_result()

    def _qdrant_retrieve(self, query: str, k: int) -> Dict:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vector = model.encode(query).tolist()

            results = self._client.search(
                collection_name="anbu_medical",
                query_vector=vector,
                limit=k,
            )

            chunks = []
            sources = set()
            for r in results:
                text = r.payload.get("text", "")
                src = r.payload.get("source", "")
                chunks.append({"text": text, "score": r.score, "source": src})
                if src:
                    sources.add(src)

            context = "\n\n".join(c["text"] for c in chunks)
            return {
                "context_str":      context,
                "retrieved_chunks": chunks,
                "sources":          list(sources),
                "num_chunks":       len(chunks),
                "retrieval_method": "qdrant_cloud",
            }
        except Exception as e:
            logger.warning(f"[CHITTA] Qdrant search failed: {e}")
            return self._empty_result()

    def _chroma_retrieve(self, query: str, k: int) -> Dict:
        try:
            col = self._chroma.get_or_create_collection("anbu_medical")
            results = col.query(query_texts=[query], n_results=min(k, col.count()))
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            chunks = [{"text": d, "source": m.get("source", ""), "score": 0.8} for d, m in zip(docs, metas)]
            sources = list({m.get("source", "") for m in metas if m.get("source")})
            return {
                "context_str":      "\n\n".join(docs),
                "retrieved_chunks": chunks,
                "sources":          sources,
                "num_chunks":       len(chunks),
                "retrieval_method": "chromadb_fallback",
            }
        except Exception as e:
            logger.warning(f"[CHITTA] Chroma search failed: {e}")
            return self._empty_result()

    def _empty_result(self) -> Dict:
        return {
            "context_str":      "",
            "retrieved_chunks": [],
            "sources":          [],
            "num_chunks":       0,
            "retrieval_method": "no_vectordb",
        }
