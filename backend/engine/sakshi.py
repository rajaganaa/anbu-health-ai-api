"""
engine/sakshi.py — Sakshi: Hallucination Verifier
Anbu Health AI — Antahkarana Pipeline v2.0
"""
import re
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

DISCLAIMERS = {
    "en": "⚠️ AI-generated info only. Consult your doctor before taking any medicine.",
    "ta": "⚠️ இது மட்டும் தகவல். மருந்து எடுக்கும் முன் Doctor கிட்ட போயி பாருங்க.",
}

HALLUCINATION_PATTERNS = [
    r'\b100%\s+cure\b',
    r'\bguaranteed\b',
    r'\bno side effects\b',
    r'\bcompletely safe\b',
    r'\bnever causes\b',
    r'\balways works\b',
]

class Sakshi:
    """Hallucination detector and final answer verifier."""

    def verify(
        self,
        question: str,
        draft_answer: str,
        context_str: str,
        sources: List[str],
        buddhi_result: Dict,
        ahamkara_result: Dict,
    ) -> Dict:

        lang = buddhi_result.get("detected_language", "en")
        flags = self._detect_hallucinations(draft_answer)
        corrected = False
        final = draft_answer

        # If hallucinations detected, add caveat
        if flags:
            caveat = " (எச்சரிக்கை: இந்த claim verify பண்ணவும்)" if lang == "ta" else " (Note: verify this claim with a doctor)"
            final = draft_answer + caveat
            corrected = True
            logger.warning(f"[SAKSHI] Hallucination flags: {flags}")

        # Build disclaimer
        disclaimer = DISCLAIMERS.get(lang, DISCLAIMERS["en"])

        # Add source note if available
        source_note = ""
        if sources:
            source_note = f" | Sources: {', '.join(sources[:2])}"

        verified = len(flags) == 0
        logger.info(f"[SAKSHI] verified={verified} flags={len(flags)}")

        return {
            "verified":            verified,
            "corrected":           corrected,
            "hallucination_flags": flags,
            "correction_note":     f"Found {len(flags)} potential hallucination(s)" if flags else "",
            "final_answer":        final,
            "sakshi_summary":      f"Answer {'verified' if verified else 'flagged'} by Sakshi",
            "medical_disclaimer":  disclaimer + source_note,
        }

    def _detect_hallucinations(self, text: str) -> List[str]:
        flags = []
        t_lower = text.lower()
        for pattern in HALLUCINATION_PATTERNS:
            if re.search(pattern, t_lower):
                flags.append(pattern)
        return flags
