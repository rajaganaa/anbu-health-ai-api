"""
engine/chitta.py — Chitta v3.0
Qdrant Cloud (primary) → Web Search fallback (Groq tool use) → Empty fallback
Fix: SentenceTransformer cached. Web search fires when Qdrant returns < 2 chunks.
"""
import os
import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# ── Cached embedding model ────────────────────────────────────────────────────
_embedding_model = None

def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        logger.info("[CHITTA] Loading SentenceTransformer (one-time)...")
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[CHITTA] Model ready")
    return _embedding_model


# ── Web search via Groq tool use ─────────────────────────────────────────────
def _web_search_fallback(query: str) -> List[Dict]:
    """
    Use Groq's built-in web search tool to get real-time medical info.
    Falls back gracefully if unavailable.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return []

    try:
        from groq import Groq
        client = Groq(api_key=api_key)

        # Use Groq with web search tool
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    f"Search for accurate Indian medical information about: {query}\n"
                    f"Focus on: CDSCO approved drugs, Indian brand names, dosages, "
                    f"side effects. Return factual medical data only. Be concise."
                )
            }],
            tools=[{
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web for current medical information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"}
                        },
                        "required": ["query"]
                    }
                }
            }],
            tool_choice="auto",
            max_tokens=600,
            temperature=0.1,
        )

        content = response.choices[0].message.content or ""
        if content.strip():
            logger.info(f"[CHITTA] Web search returned {len(content)} chars")
            return [{
                "text": content,
                "source": "web_search_groq",
                "score": 0.6
            }]
    except Exception as e:
        logger.debug(f"[CHITTA] Web search unavailable: {e}")

    # Fallback: use Groq without tools for general medical knowledge
    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an Indian medical reference. Provide factual drug "
                        "information relevant to India — Indian brand names, CDSCO "
                        "status, common usage in Tamil Nadu. Be concise and factual."
                    )
                },
                {
                    "role": "user",
                    "content": f"Provide medical reference info about: {query}"
                }
            ],
            max_tokens=400,
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        if content.strip():
            logger.info("[CHITTA] Groq knowledge fallback used")
            return [{
                "text": content,
                "source": "groq_medical_knowledge",
                "score": 0.5
            }]
    except Exception as e:
        logger.warning(f"[CHITTA] Groq fallback failed: {e}")

    return []


class Chitta:
    def __init__(self):
        self._qdrant_client = None
        self._fallback_chroma = False
        self._setup()
        # Force the embedding model to load NOW, during pipeline warm-up at
        # startup — previously this only loaded lazily on the first real
        # user query, meaning every freshly-started replica's first request
        # paid the full SentenceTransformer load cost (several seconds),
        # even though the readiness probe had already reported "ready".
        _get_embedding_model()

    def _setup(self):
        qdrant_url = os.environ.get("QDRANT_URL", "")
        qdrant_key = os.environ.get("QDRANT_API_KEY", "")

        if qdrant_url and qdrant_key:
            try:
                from qdrant_client import QdrantClient
                self._qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_key)
                logger.info("[CHITTA] Qdrant Cloud connected")
                return
            except Exception as e:
                logger.warning(f"[CHITTA] Qdrant failed: {e}")

        # ChromaDB fallback
        try:
            import chromadb
            self._chroma = chromadb.PersistentClient(path="/tmp/anbu_chroma")
            self._fallback_chroma = True
            logger.info("[CHITTA] ChromaDB fallback active")
        except Exception as e:
            logger.warning(f"[CHITTA] No vector DB available: {e}")

    def retrieve(self, question: str, entities: List[str], k: int = 5) -> Dict:
        query = question + " " + " ".join(entities[:3])

        # Step 1: Try Qdrant
        chunks = []
        method = "no_vectordb"

        if self._qdrant_client:
            chunks = self._qdrant_retrieve(query, k)
            method = "qdrant_cloud"
        elif self._fallback_chroma:
            chunks = self._chroma_retrieve(query, k)
            method = "chromadb_fallback"

        # Step 2: Web search if Qdrant returned < 2 good results
        web_chunks = []
        if len(chunks) < 2:
            logger.info(f"[CHITTA] Only {len(chunks)} chunks from DB — triggering web search")
            web_chunks = _web_search_fallback(question)
            if web_chunks:
                method = method + "+web_search"

        all_chunks = chunks + web_chunks
        context    = "\n\n".join(c["text"] for c in all_chunks)
        sources    = list(dict.fromkeys(c["source"] for c in all_chunks))

        logger.info(f"[CHITTA] method={method} db_chunks={len(chunks)} web_chunks={len(web_chunks)}")

        return {
            "context_str":      context,
            "retrieved_chunks": all_chunks,
            "sources":          sources,
            "num_chunks":       len(all_chunks),
            "retrieval_method": method,
        }

    def _qdrant_retrieve(self, query: str, k: int) -> List[Dict]:
        try:
            model  = _get_embedding_model()
            vector = model.encode(query).tolist()
            results = self._qdrant_client.search(
                collection_name="anbu_medical",
                query_vector=vector,
                limit=k,
            )
            chunks  = []
            for r in results:
                text = r.payload.get("text", "")
                src  = r.payload.get("source", "")
                if text:
                    chunks.append({"text": text, "score": r.score, "source": src})
            return chunks
        except Exception as e:
            logger.warning(f"[CHITTA] Qdrant search failed: {e}")
            return []

    def _chroma_retrieve(self, query: str, k: int) -> List[Dict]:
        try:
            col     = self._chroma.get_or_create_collection("anbu_medical")
            count   = col.count()
            if count == 0:
                return []
            results = col.query(query_texts=[query], n_results=min(k, count))
            docs    = results.get("documents", [[]])[0]
            metas   = results.get("metadatas", [[]])[0]
            return [{"text": d, "source": m.get("source",""), "score": 0.7}
                    for d, m in zip(docs, metas)]
        except Exception as e:
            logger.warning(f"[CHITTA] Chroma failed: {e}")
            return []

    def _empty_result(self) -> Dict:
        return {
            "context_str": "", "retrieved_chunks": [],
            "sources": [], "num_chunks": 0,
            "retrieval_method": "no_vectordb",
        }