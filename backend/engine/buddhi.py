"""
engine/buddhi.py — Buddhi: Core Reasoner (Groq llama-3.3-70b-versatile)
Anbu Health AI — Antahkarana Pipeline v2.0

Buddhi = discriminative intellect (Indian philosophy)
Multi-pass: Tarka (Pass1) → Pramana (Pass2) → Samsaya (Pass3)

Features:
  ✅ Tanglish (Tamil+English mix) responses
  ✅ Structured format: SUMMARY/USES/DOSAGE/SIDE_EFFECTS/WARNINGS/NOTES
  ✅ Mode-specific prompts: lab | scan | medicine | general
  ✅ Concise answers — no paragraph dumps
  ✅ Tamil Unicode detection + keyword detection

Author: Rajaganapathy M | Patent: 202641043947
"""

import os
import re
import logging
import time
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Language detection ─────────────────────────────────────────────────────────
TAMIL_RE = re.compile(r'[\u0B80-\u0BFF]')

def detect_language(text: str) -> str:
    if len(TAMIL_RE.findall(text)) >= 3:
        return "ta"
    if re.search(r'\bin tamil\b|\btamil\b|\bதமிழ்\b|\btanglish\b', text, re.IGNORECASE):
        return "ta"
    return "en"

# ── Groq Engine ────────────────────────────────────────────────────────────────
class GroqEngine:
    def __init__(self):
        from groq import Groq
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = GROQ_MODEL
        logger.info(f"[BUDDHI] Groq ready — {self.model}")

    def chat(self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

_engine = None
def _get_engine():
    global _engine
    if _engine is None:
        _engine = GroqEngine()
    return _engine

# ── System Prompts ─────────────────────────────────────────────────────────────

_BASE_RULES = """
STRICT RULES — follow exactly:
1. Respond in TANGLISH (Tamil+English mix) — some words Tamil, some English
2. MAXIMUM 2 sentences in ANSWER field — no long paragraphs
3. Use EXACT structured format below — no skipping sections
4. DOSAGE must include exact mg amounts and frequency
5. USES maximum 4 items
6. SIDE_EFFECTS maximum 4 items
7. WARNINGS maximum 3 items
8. If user asks in Tamil → respond more in Tamil
9. If user asks in English → Tanglish mix
10. Never say "not in context" — use your medical knowledge
"""

_FORMAT_MEDICINE = _BASE_RULES + """
MEDICINE RESPONSE FORMAT:
SUMMARY: <one sentence about the medicine in Tanglish>
USES: <use1>, <use2>, <use3>
DOSAGE: <exact dose — Adults: Xmg every Yhrs, Children: Zmg>
SIDE_EFFECTS: <effect1>, <effect2>, <effect3>
WARNINGS: <warning1>, <warning2>
NOTES: <one important note or "None">
ANSWER: <2 sentences max in Tanglish — what this medicine is and main use>
"""

_FORMAT_LAB = _BASE_RULES + """
LAB REPORT RESPONSE FORMAT:
SUMMARY: <one sentence — overall result normal/abnormal>
FINDINGS: <finding1 with value>, <finding2 with value>, <finding3 with value>
RECOMMENDATION: <what patient should do — food/follow-up/medicine>
URGENCY: low|medium|high
NOTES: <any important warning>
ANSWER: <2 sentences max in Tanglish — what the report says and what to do>
"""

_FORMAT_SCAN = _BASE_RULES + """
SCAN/XRAY RESPONSE FORMAT:
SUMMARY: <one sentence — what was seen in scan>
FINDINGS: <finding1>, <finding2>, <finding3>
RECOMMENDATION: <what patient should do next>
URGENCY: low|medium|high
NOTES: <important warning>
ANSWER: <2 sentences max in Tanglish — scan result and next step>
"""

_FORMAT_GENERAL = _BASE_RULES + """
GENERAL MEDICAL RESPONSE FORMAT:
SUMMARY: <one sentence answer>
DETAILS: <3-4 key points separated by |>
RECOMMENDATION: <what patient should do>
NOTES: <any warning or disclaimer>
ANSWER: <2 sentences max in Tanglish — direct answer to the question>
"""

_FORMAT_INTERACTION = _BASE_RULES + """
DRUG INTERACTION RESPONSE FORMAT:
SUMMARY: <safe or dangerous — one sentence>
RISK_LEVEL: safe|caution|dangerous
DETAILS: <explain the interaction>
RECOMMENDATION: <what patient should do>
ANSWER: <2 sentences max — is it safe and what to do>
"""

SYSTEM_PROMPTS = {
    "medicine":    "You are MedAssist, expert medical AI for Tamil Nadu village clinic. " + _FORMAT_MEDICINE,
    "lab":         "You are MedAssist, expert lab report analyzer for Tamil Nadu village clinic. " + _FORMAT_LAB,
    "scan":        "You are MedAssist, expert radiologist AI for Tamil Nadu village clinic. " + _FORMAT_SCAN,
    "general":     "You are MedAssist, expert medical AI for Tamil Nadu village clinic. " + _FORMAT_GENERAL,
    "interaction": "You are MedAssist, expert pharmacist AI for Tamil Nadu village clinic. " + _FORMAT_INTERACTION,
    "dosage":      "You are MedAssist, expert dosage calculator for Tamil Nadu village clinic. " + _FORMAT_MEDICINE,
}

PRAMANA_SYSTEM = (
    "You are a strict medical fact verifier.\n"
    "Format:\nSupported: yes/no\nEvidence: <max 15 words>\nRevised answer: <answer>"
)

# ── Structured response parser ─────────────────────────────────────────────────
def _parse_structured(raw: str, mode: str, draft: str) -> Dict:
    def field(label: str) -> str:
        m = re.search(rf'{label}\s*:\s*(.+?)(?=\n[A-Z_]{{3,}}\s*:|$)', raw, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else ""

    def items(text: str) -> List[str]:
        if not text:
            return []
        return [i.strip() for i in re.split(r',\s*|\n[-•*]\s*|\n\d+\.\s*|\|', text) if i.strip()]

    answer_m = re.search(r'ANSWER\s*:\s*(.+?)$', raw, re.IGNORECASE | re.DOTALL)
    full_answer = answer_m.group(1).strip() if answer_m else draft

    base = {
        "summary":     field("SUMMARY") or draft[:150],
        "full_answer": full_answer,
        "notes":       field("NOTES") if field("NOTES").lower() not in ("none", "இல்லை", "") else "",
        "recommendation": field("RECOMMENDATION"),
    }

    if mode == "medicine":
        base.update({
            "uses":         items(field("USES")),
            "dosage":       field("DOSAGE"),
            "side_effects": items(field("SIDE_EFFECTS")),
            "warnings":     items(field("WARNINGS")),
        })
    elif mode in ("lab", "scan"):
        base.update({
            "findings": items(field("FINDINGS")),
            "urgency":  field("URGENCY") or "low",
        })
    elif mode == "interaction":
        base.update({
            "risk_level": field("RISK_LEVEL") or "caution",
            "details":    field("DETAILS"),
        })
    elif mode == "general":
        base.update({
            "details": items(field("DETAILS")),
        })

    return base

# ── Buddhi Class ───────────────────────────────────────────────────────────────
class Buddhi:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = _get_engine()
        return self._engine

    def reason(
        self,
        question: str,
        context_str: str,
        q_type: str,
        mode: str = "general",
        vision_info: Optional[Dict] = None,
    ) -> Dict:
        t0 = time.time()
        lang = detect_language(question)

        # Enrich context with vision info
        vision_ctx = ""
        if vision_info and not vision_info.get("error"):
            parts = []
            for k in ("drug_name", "brand_name", "generic_name", "strength", "form",
                      "manufacturer", "expiry_date", "summary"):
                v = vision_info.get(k, "")
                if v and v not in ("Not visible", "Not detected", ""):
                    parts.append(f"{k}: {v}")
            if parts:
                vision_ctx = "[Vision Extracted: " + " | ".join(parts) + "]\n\n"

        full_ctx = vision_ctx + context_str if vision_ctx else context_str
        system = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])

        # Tamil suffix
        if lang == "ta":
            system += "\n\nமுக்கியம்: பதில்களை Tanglish-ல் கொடு (Tamil+English mix). தெளிவாக, குறுக்காக இரு."

        user_prompt = f"Medical Context:\n{full_ctx}\n\nQuestion: {question}"

        # ── Pass 1: Tarka ──────────────────────────────────────────────────────
        pass1_raw = self.engine.chat(system, user_prompt, max_tokens=1024)
        pass1_answer = self._extract_answer(pass1_raw)

        # ── Pass 2: Pramana (verify against context) ───────────────────────────
        pass2_fired = False
        pass2_answer = pass1_answer
        pass2_verified = True

        if full_ctx.strip() and not self._is_bad(pass1_answer):
            pramana_user = (
                f"Question: {question}\n\n"
                f"Draft answer: {pass1_answer}\n\n"
                f"Context:\n{full_ctx[:1500]}"
            )
            pass2_raw = self.engine.chat(PRAMANA_SYSTEM, pramana_user, max_tokens=256)
            pass2_answer, pass2_verified = self._parse_pramana(pass2_raw, pass1_answer)
            pass2_fired = True

        # ── Pass 3: Samsaya (fallback if bad answer) ───────────────────────────
        pass3_fired = False
        final_answer = pass2_answer

        if self._is_bad(pass2_answer):
            for _ in range(2):
                alt_raw = self.engine.chat(system, user_prompt, max_tokens=512, temperature=0.5)
                alt = self._extract_answer(alt_raw)
                if not self._is_bad(alt):
                    final_answer = alt
                    pass3_fired = True
                    break

        elapsed = round(time.time() - t0, 3)
        structured = _parse_structured(pass1_raw, mode, final_answer)

        if structured.get("full_answer") and not self._is_bad(structured["full_answer"]):
            final_answer = structured["full_answer"]

        # Tamil disclaimer
        if lang == "ta":
            structured.setdefault("notes", "இந்த தகவல் பொதுவான நோக்கங்களுக்காக மட்டுமே. Doctor கிட்ட போயி confirm பண்ணுங்க.")

        return {
            "draft_answer":        final_answer,
            "pass1_answer":        pass1_answer,
            "pass2_fired":         pass2_fired,
            "pass2_verified":      pass2_verified,
            "pass3_fired":         pass3_fired,
            "structured_response": structured,
            "detected_language":   lang,
            "model":               getattr(self._engine, "model", GROQ_MODEL),
            "latency_s":           elapsed,
        }

    def _extract_answer(self, raw: str) -> str:
        m = re.search(r'ANSWER\s*:\s*(.+?)$', raw, re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r'[.,;:]+$', '', m.group(1).strip()).strip()
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        return lines[-1] if lines else raw[:400]

    def _parse_pramana(self, raw: str, draft: str) -> Tuple[str, bool]:
        sup_m = re.search(r'Supported\s*:\s*(yes|no)', raw, re.IGNORECASE)
        rev_m = re.search(r'Revised answer\s*:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
        if rev_m and sup_m and sup_m.group(1).lower() == "no":
            rev = rev_m.group(1).strip()
            if rev and not self._is_bad(rev):
                return rev, False
        return draft, True

    BAD_PATTERNS = [
        "i don't know", "i cannot", "i am not sure", "no information",
        "sorry", "cannot determine", "not available", "i'm not aware",
        "தெரியவில்லை", "முடியாது", "தகவல் இல்லை",
    ]

    def _is_bad(self, answer: str) -> bool:
        if not answer or len(answer.strip()) < 10:
            return True
        return any(p in answer.lower() for p in self.BAD_PATTERNS)
