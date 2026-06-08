"""
rag/anbu_rag.py — Qdrant Cloud RAG Indexer
Anbu Health AI v2.0

Indexes Indian medical PDFs into Qdrant Cloud.
Falls back to ChromaDB if Qdrant unavailable.
"""
import os
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

COLLECTION_NAME = "anbu_medical"
DATA_DIR = Path(os.environ.get("ANBU_DATA_DIR", "./data/drug_guides"))
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _chunk_text(text: str) -> List[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), CHUNK_SIZE - CHUNK_OVERLAP):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        if len(chunk.strip()) > 50:
            chunks.append(chunk)
    return chunks


def _extract_text(pdf_path: str) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        return "\n".join(page.get_text() for page in doc)
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        logger.error(f"[RAG] PDF extract failed {pdf_path}: {e}")
        return ""


def _get_embedding_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")


def build_index_if_needed():
    """Build Qdrant index from PDFs if not already built."""
    if not DATA_DIR.exists():
        logger.warning(f"[RAG] Data dir not found: {DATA_DIR}")
        return

    pdfs = list(DATA_DIR.glob("*.pdf"))
    if not pdfs:
        logger.warning("[RAG] No PDFs found in data directory")
        return

    qdrant_url = os.environ.get("QDRANT_URL", "")
    qdrant_key = os.environ.get("QDRANT_API_KEY", "")

    if qdrant_url and qdrant_key:
        _build_qdrant_index(pdfs, qdrant_url, qdrant_key)
    else:
        _build_chroma_index(pdfs)


def _build_qdrant_index(pdfs: List[Path], url: str, api_key: str):
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct

        client = QdrantClient(url=url, api_key=api_key)
        model = _get_embedding_model()

        # Create collection if not exists
        existing = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in existing:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )
            logger.info(f"[RAG] Created Qdrant collection: {COLLECTION_NAME}")
        else:
            # Check if already populated
            count = client.count(COLLECTION_NAME).count
            if count > 100:
                logger.info(f"[RAG] Qdrant already has {count} vectors — skipping rebuild")
                return

        points = []
        point_id = 0
        for pdf_path in pdfs:
            text = _extract_text(str(pdf_path))
            if not text.strip():
                continue
            chunks = _chunk_text(text)
            logger.info(f"[RAG] Indexing {pdf_path.name} — {len(chunks)} chunks")
            for chunk in chunks:
                vector = model.encode(chunk).tolist()
                points.append(PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"text": chunk, "source": pdf_path.name},
                ))
                point_id += 1

        if points:
            # Upload in batches
            batch_size = 100
            for i in range(0, len(points), batch_size):
                client.upsert(collection_name=COLLECTION_NAME, points=points[i:i+batch_size])
            logger.info(f"[RAG] Qdrant index built: {point_id} vectors")

    except Exception as e:
        logger.error(f"[RAG] Qdrant index failed: {e}")
        _build_chroma_index(pdfs)


def _build_chroma_index(pdfs: List[Path]):
    try:
        import chromadb
        client = chromadb.PersistentClient(path="/tmp/anbu_chroma")
        col = client.get_or_create_collection("anbu_medical")

        if col.count() > 50:
            logger.info(f"[RAG] ChromaDB already has {col.count()} docs — skipping rebuild")
            return

        for pdf_path in pdfs:
            text = _extract_text(str(pdf_path))
            if not text.strip():
                continue
            chunks = _chunk_text(text)
            logger.info(f"[RAG] ChromaDB indexing {pdf_path.name} — {len(chunks)} chunks")
            ids = [f"{pdf_path.stem}_{i}" for i in range(len(chunks))]
            metas = [{"source": pdf_path.name} for _ in chunks]
            col.add(documents=chunks, ids=ids, metadatas=metas)

        logger.info(f"[RAG] ChromaDB index built: {col.count()} docs")
    except Exception as e:
        logger.error(f"[RAG] ChromaDB fallback also failed: {e}")
