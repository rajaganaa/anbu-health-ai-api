"""
engine/ahamkara.py — Confidence Scorer
engine/sakshi.py is in a separate file
"""
import re
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class Ahamkara:
    """Confidence scorer — rates answer quality 0-100."""

    def score(self, buddhi_result: Dict, chitta_result: Dict, question: str) -> Dict:
        score = 50  # base

        # Context quality
        if chitta_result["num_chunks"] >= 3:
            score += 15
        elif chitta_result["num_chunks"] >= 1:
            score += 8

        # Pass2 verification
        if buddhi_result.get("pass2_verified"):
            score += 15
        if buddhi_result.get("pass2_fired"):
            score += 5

        # Answer quality
        answer = buddhi_result.get("draft_answer", "")
        if len(answer) > 50:
            score += 10
        if len(answer) > 150:
            score += 5

        # Structured response completeness
        sr = buddhi_result.get("structured_response", {})
        filled = sum(1 for v in sr.values() if v)
        score += min(filled * 3, 15)

        score = min(score, 98)

        if score >= 75:
            label = "High Confidence"
        elif score >= 50:
            label = "Medium Confidence"
        else:
            label = "Low Confidence"

        return {
            "confidence_score": score,
            "confidence_label": label,
            "context_chunks":   chitta_result["num_chunks"],
            "pass2_verified":   buddhi_result.get("pass2_verified", True),
        }
