"""
engine/manas.py — Manas: Question Router
Anbu Health AI — Antahkarana Pipeline v2.0
"""
import re
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)

class QType:
    SIMPLE       = "simple"
    MEDICAL      = "medical"
    DOSAGE       = "dosage"
    EXPIRY       = "expiry"
    INTERACTION  = "interaction"
    LAB          = "lab"
    SCAN         = "scan"
    COMPARISON   = "comparison"
    VERIFICATION = "verification"

DOSAGE_KW    = {"dose", "dosage", "how much", "how many mg", "calculate", "mg", "kg", "weight", "tablet", "syrup"}
EXPIRY_KW    = {"expire", "expiry", "expired", "expiration", "use by", "best before", "still good"}
INTERACT_KW  = {"interaction", "together", "combine", "mix", "safe with", "react", "side effect with"}
MEDICAL_KW   = {"drug", "medicine", "medication", "tablet", "capsule", "symptoms", "treatment", "fever", "pain", "sugar", "bp", "diabetes", "pressure"}

class Manas:
    def route(self, question: str, mode: str = "general") -> dict:
        q = question.lower()
        entities = self._extract_entities(question)

        # Mode takes priority for routing
        if mode in ("lab", "scan"):
            q_type = QType.LAB if mode == "lab" else QType.SCAN
        elif any(k in q for k in EXPIRY_KW):
            q_type = QType.EXPIRY
        elif any(k in q for k in INTERACT_KW):
            q_type = QType.INTERACTION
        elif any(k in q for k in DOSAGE_KW):
            q_type = QType.DOSAGE
        elif any(k in q for k in MEDICAL_KW):
            q_type = QType.MEDICAL
        else:
            q_type = QType.SIMPLE

        logger.info(f"[MANAS] type={q_type} mode={mode} entities={entities[:3]}")
        return {
            "question_type": q_type,
            "mode":          mode,
            "entities":      entities,
            "confidence":    0.9,
        }

    def _extract_entities(self, text: str) -> List[str]:
        # Extract capitalized words (likely drug/medicine names)
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b', text)
        # Common medicine keywords
        medicine_re = re.compile(
            r'\b(paracetamol|ibuprofen|amoxicillin|metformin|atorvastatin|'
            r'omeprazole|pantoprazole|cetirizine|azithromycin|dolo|crocin|'
            r'augmentin|zyrtec|ciprofloxacin|aspirin|insulin)\b',
            re.IGNORECASE
        )
        meds = [m.group(0) for m in medicine_re.finditer(text)]
        return list(set(words + meds))[:5]
