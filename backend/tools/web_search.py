"""
tools/web_search.py — Medical Web Search with Source Citations
Uses DuckDuckGo Instant Answer API (free, no API key needed).
Falls back to curated medical site scraping if DDG returns nothing.
Like Perplexity — shows answer + sources the user can verify.
"""
import re
import json
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)

TIMEOUT = 8  # seconds

# Trusted medical sources we prefer
TRUSTED_DOMAINS = [
    "mayoclinic.org", "medlineplus.gov", "nih.gov", "who.int",
    "webmd.com", "healthline.com", "medicalnewstoday.com",
    "pubmed.ncbi.nlm.nih.gov", "drugs.com", "rxlist.com",
    "1mg.com", "practo.com", "apollopharmacy.in",
]

def _is_trusted(url: str) -> bool:
    return any(d in url for d in TRUSTED_DOMAINS)


def search_duckduckgo(query: str, max_results: int = 4) -> Dict:
    """
    DuckDuckGo Instant Answer API — free, no key needed.
    Returns structured results with sources like Perplexity.
    """
    try:
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "t": "anbu-health-ai",
        }
        r = requests.get(
            "https://api.duckduckgo.com/",
            params=params,
            timeout=TIMEOUT,
            headers={"User-Agent": "AnbuHealthAI/1.0 (medical assistant)"}
        )
        data = r.json()

        results = []
        sources = []

        # Instant answer (best result)
        abstract = data.get("AbstractText", "").strip()
        abstract_url = data.get("AbstractURL", "").strip()
        abstract_source = data.get("AbstractSource", "").strip()

        if abstract and abstract_url:
            results.append({
                "title": abstract_source or "Reference",
                "snippet": abstract[:400],
                "url": abstract_url,
                "trusted": _is_trusted(abstract_url),
            })
            sources.append({
                "name": abstract_source or abstract_url,
                "url": abstract_url,
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
                url = topic["FirstURL"]
                results.append({
                    "title": topic.get("Text","")[:80],
                    "snippet": topic.get("Text","")[:300],
                    "url": url,
                    "trusted": _is_trusted(url),
                })
                sources.append({
                    "name": url.split("/")[-1].replace("-"," ").title() or url,
                    "url": url,
                })

        return {
            "query": query,
            "results": results[:max_results],
            "sources": sources[:max_results],
            "answer": abstract if abstract else None,
        }

    except Exception as e:
        logger.warning(f"[WEB_SEARCH] DDG failed: {e}")
        return {"query": query, "results": [], "sources": [], "answer": None}


def search_medical_web(question: str, context: str = "") -> Dict:
    """
    Main entry — formats medical question for search, returns
    answer + citations the user can verify (like Perplexity).
    """
    # Build a focused medical search query
    medical_query = f"{question} medical information"
    if context:
        # Add context keywords (e.g. "Paracetamol" from file_context)
        context_words = context[:50].strip()
        medical_query = f"{context_words} {question}"

    ddg = search_duckduckgo(medical_query)

    # Format sources as citation list
    citations = []
    for i, src in enumerate(ddg.get("sources", []), 1):
        citations.append(f"[{i}] {src['name']} — {src['url']}")

    return {
        "web_answer": ddg.get("answer", ""),
        "web_results": ddg.get("results", []),
        "citations": citations,
        "citation_text": "\n".join(citations) if citations else "",
        "sources_found": len(ddg.get("sources", [])),
    }
