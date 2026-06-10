"""
engine/buddhi.py — Buddhi v2.3 FINAL
ROOT CAUSE FIX: Vision-extracted data now passed as ACTUAL REPORT DATA to LLM.
The LLM now sees real values (e.g. WBC: 5390, HbA1c: 6.5%) and explains them.
No more generic "doctor போங்க" for everything.
"""
import os, re, json, logging, time
from typing import Optional, Dict

logger = logging.getLogger(__name__)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
TAMIL_RE   = re.compile(r'[\u0B80-\u0BFF]')

def detect_language(text: str) -> str:
    if len(TAMIL_RE.findall(text)) >= 3: return "ta"
    if re.search(r'\bin tamil\b|\btamil\b|\bதமிழ்\b', text, re.IGNORECASE): return "ta"
    return "en"

class GroqEngine:
    def __init__(self):
        from groq import Groq
        api_key = os.environ.get("GROQ_API_KEY","")
        if not api_key: raise RuntimeError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model  = GROQ_MODEL

    def chat(self, system, user, max_tokens=1400, temperature=0.1):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            max_tokens=max_tokens, temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

_engine = None
def _get_engine():
    global _engine
    if _engine is None: _engine = GroqEngine()
    return _engine

# ── SYSTEM PROMPTS ─────────────────────────────────────────────────────────────

_LAB_SYSTEM = """You are Anbu Health AI for Tamil Nadu village patients.
You will receive ACTUAL LAB REPORT DATA extracted from the patient's report.
Use ONLY the provided data — never invent values.

RULES:
- Report EVERY test value provided with its status (High/Low/Normal)
- Group by: Diabetes, Cholesterol, Blood Count, Liver, Kidney
- Clearly mark abnormal values first
- Do NOT prescribe medicines
- Recommend doctor for abnormal values

LANGUAGE: Tanglish (Tamil+English mix) — simple for village patients.

Return ONLY valid JSON (no text before/after):
{
  "mode": "lab",
  "urgency": "low|medium|high",
  "confidence": 85,
  "summary": "2 sentence Tanglish overview of key findings",
  "findings": ["TestName: value unit (HIGH/LOW/NORMAL — explain briefly)"],
  "abnormal_findings": ["only abnormal tests with values"],
  "normal_findings": ["only normal tests"],
  "recommendation": "Specific Tanglish advice based on actual results",
  "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
  "answer": "3-4 sentence helpful Tanglish explanation of what the report means"
}"""

_SCAN_SYSTEM = """You are Anbu Health AI for Tamil Nadu village patients.
You will receive data from a medical scan or X-ray analysis.

CRITICAL RULES:
- If surgical hardware (plates/screws/rods) detected → explicitly state post-surgical status
- NEVER prescribe medicines from X-rays
- NEVER invent dosage
- If uncertain → say "Doctor review தேவை"

LANGUAGE: Tanglish (Tamil+English mix).

Return ONLY valid JSON:
{
  "mode": "scan",
  "urgency": "low|medium|high",
  "confidence": 70,
  "body_part": "identified body part",
  "scan_type": "X-ray|CT|MRI|Ultrasound",
  "summary": "Tanglish summary of what scan shows",
  "findings": ["clear finding 1", "clear finding 2"],
  "implants_detected": false,
  "implant_details": "describe hardware or null",
  "fractures_visible": false,
  "recommendation": "Next step — NO medicine/dosage",
  "disclaimer": "⚠️ Radiologist/Orthopedic surgeon confirm பண்ணுங்க.",
  "answer": "3-4 sentence Tanglish explanation for patient"
}"""

_MEDICINE_SYSTEM = """You are Anbu Health AI for Tamil Nadu village patients.
You will receive medicine identification data.

RULES:
- Give REAL medicine information — uses, side effects, warnings
- NEVER invent dosage numbers
- Always say doctor prescription required for dosage

LANGUAGE: Tanglish (Tamil+English mix).

Return ONLY valid JSON:
{
  "mode": "medicine",
  "urgency": "low",
  "confidence": 85,
  "medicine_identified": true,
  "drug_name": "medicine name",
  "drug_category": "antacid/antibiotic/painkiller/etc",
  "summary": "Tanglish — what this medicine is",
  "uses": ["specific use 1", "specific use 2", "specific use 3"],
  "side_effects": ["effect 1", "effect 2", "effect 3"],
  "warnings": ["warning 1", "warning 2"],
  "dosage": null,
  "recommendation": "Doctor prescription follow பண்ணுங்க",
  "disclaimer": "⚠️ Doctor prescription இல்லாம எடுக்க வேண்டாம்.",
  "answer": "3-4 sentence Tanglish about this medicine and its main use"
}"""

_GENERAL_SYSTEM = """You are Anbu Health AI for Tamil Nadu village patients.
Give helpful, accurate medical information. Simple language. Never diagnose from symptoms alone.

LANGUAGE: Tanglish (Tamil+English mix).

Return ONLY valid JSON:
{
  "mode": "general",
  "urgency": "low|medium|high",
  "confidence": 80,
  "summary": "Direct answer",
  "details": ["specific point 1", "specific point 2", "specific point 3"],
  "recommendation": "What patient should do",
  "disclaimer": "⚠️ Doctor advice follow பண்ணுங்க.",
  "answer": "3-4 sentence helpful Tanglish answer with actual medical info"
}"""

SYSTEM_PROMPTS = {
    "lab": _LAB_SYSTEM, "scan": _SCAN_SYSTEM,
    "medicine": _MEDICINE_SYSTEM, "general": _GENERAL_SYSTEM
}

# ── JSON Parser ────────────────────────────────────────────────────────────────
def _parse_json(raw: str, mode: str) -> Dict:
    if not raw: return _fallback(mode)
    clean = re.sub(r'```(?:json)?\s*','',raw).strip().strip('`').strip()
    for text in [clean, clean[clean.find('{'):clean.rfind('}')+1] if '{' in clean else None]:
        if not text: continue
        try: return json.loads(text)
        except Exception: pass
    logger.warning(f"[BUDDHI] JSON parse failed, using regex. mode={mode}")
    return _extract_regex(raw, mode)

def _extract_regex(raw: str, mode: str) -> Dict:
    def g(k): m=re.search(rf'"{k}"\s*:\s*"([^"]*)"',raw); return m.group(1) if m else ""
    def gl(k):
        m=re.search(rf'"{k}"\s*:\s*\[([^\]]*)\]',raw,re.DOTALL)
        return re.findall(r'"([^"]+)"',m.group(1)) if m else []
    ans = g("answer") or g("summary") or raw[:400]
    return {
        "mode":mode,"urgency":g("urgency") or "low","confidence":65,
        "summary":g("summary") or ans,"findings":gl("findings"),"details":gl("details"),
        "uses":gl("uses"),"side_effects":gl("side_effects"),"warnings":gl("warnings"),
        "recommendation":g("recommendation") or "Doctor கிட்ட போங்க.",
        "disclaimer":g("disclaimer") or "⚠️ Doctor confirm பண்ணுங்க.",
        "answer":ans,"implants_detected":False,"fractures_visible":False,
        "body_part":g("body_part"),"scan_type":g("scan_type"),
        "drug_name":g("drug_name"),"dosage":None,"medicine_identified":True,
        "abnormal_findings":gl("abnormal_findings"),"normal_findings":gl("normal_findings"),
    }

def _fallback(mode: str) -> Dict:
    return {
        "mode":mode,"urgency":"low","confidence":0,
        "summary":"Analysis மீண்டும் try பண்ணுங்க.",
        "findings":[],"details":[],"uses":[],"side_effects":[],"warnings":[],
        "abnormal_findings":[],"normal_findings":[],
        "recommendation":"Doctor கிட்ட போங்க.",
        "disclaimer":"⚠️ Doctor confirm பண்ணுங்க.",
        "answer":"Sorry, analysis fail ஆச்சு. மீண்டும் try பண்ணுங்க.",
    }

# ── Vision Context Builder ─────────────────────────────────────────────────────
def _build_vision_context(vision_info: Dict, mode: str) -> str:
    """Convert vision output into detailed context for LLM."""
    if not vision_info or vision_info.get("error"): return ""

    lines = []

    if mode == "lab":
        # Pass actual test values to LLM
        tests = vision_info.get("tests", [])
        if tests:
            lines.append("=== ACTUAL LAB REPORT VALUES ===")
            for t in tests:
                name   = t.get("name","")
                value  = t.get("value","")
                unit   = t.get("unit","")
                ref    = t.get("range","")
                status = t.get("status","").upper()
                if name and value:
                    lines.append(f"  {name}: {value} {unit} | Ref: {ref} | Status: {status}")
            lines.append(f"Abnormal count: {vision_info.get('abnormal_count',0)}")
            lines.append(f"Overall status: {vision_info.get('overall_status','unknown')}")
        # Always include raw summary/text — handles scrambled Indian lab PDFs
        summary = vision_info.get("summary","")
        raw_text = vision_info.get("raw_text","")
        if summary: lines.append(f"Summary: {summary}")
        if raw_text and not tests:
            lines.append("=== RAW LAB REPORT TEXT (parse carefully) ===")
            lines.append(raw_text[:3000])
        lines.append("TASK: Extract ALL test values, compare to reference ranges, explain abnormal values in simple Tanglish.")

    elif mode == "scan":
        lines.append("=== SCAN/X-RAY ANALYSIS ===")
        for k in ("scan_type","body_part","impression"):
            v = vision_info.get(k,"")
            if v: lines.append(f"  {k}: {v}")
        findings = vision_info.get("findings",[])
        if findings: lines.append(f"  Findings: {', '.join(str(f) for f in findings)}")
        abnorm = vision_info.get("abnormalities",[])
        if abnorm: lines.append(f"  Abnormalities: {', '.join(str(a) for a in abnorm)}")
        lines.append("TASK: Explain what these findings mean for the patient. If hardware visible, mention post-surgical status.")

    elif mode == "medicine":
        lines.append("=== MEDICINE IDENTIFIED ===")
        for k in ("drug_name","brand_name","generic_name","strength","form","drug_category","manufacturer"):
            v = vision_info.get(k,"")
            if v and v not in ("Not visible","Not detected",""): lines.append(f"  {k}: {v}")
        s = vision_info.get("summary","")
        if s: lines.append(f"  Summary: {s}")
        lines.append("TASK: Explain this medicine — uses, side effects, warnings in Tanglish.")

    if not lines:
        s = vision_info.get("summary","")
        if s: lines.append(f"Context: {s}")

    return "\n".join(lines) + "\n\n" if lines else ""

# ── Buddhi Class ───────────────────────────────────────────────────────────────
class Buddhi:
    def __init__(self): self._engine = None

    @property
    def engine(self):
        if self._engine is None: self._engine = _get_engine()
        return self._engine

    def reason(self, question, context_str, q_type, mode="general", vision_info=None):
        t0   = time.time()
        lang = detect_language(question)
        system = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])

        vision_ctx = _build_vision_context(vision_info or {}, mode)
        rag_ctx    = f"Medical Reference:\n{context_str}\n\n" if context_str.strip() else ""
        lang_note  = "\nIMPORTANT: Write answer in Tanglish (Tamil+English mix)." if lang == "ta" else "\nWrite in Tanglish (Tamil+English mix)."

        user_prompt = (
            f"{vision_ctx}"
            f"{rag_ctx}"
            f"Patient question: {question}"
            f"{lang_note}\n\n"
            f"Return valid JSON only. No text before or after the JSON."
        )

        try:
            raw = self.engine.chat(system, user_prompt, max_tokens=1400)
        except Exception as e:
            logger.error(f"[BUDDHI] LLM failed: {e}")
            raw = ""

        parsed     = _parse_json(raw, mode)
        answer     = parsed.get("answer") or parsed.get("summary") or "மீண்டும் try பண்ணுங்க."
        confidence = int(str(parsed.get("confidence","70")).replace("%","")) if parsed.get("confidence") else 70

        sr = {
            "summary":        parsed.get("summary",""),
            "full_answer":    answer,
            "findings":       parsed.get("findings") or parsed.get("details") or [],
            "recommendation": parsed.get("recommendation",""),
            "urgency":        parsed.get("urgency","low"),
            "confidence":     confidence,
            "disclaimer":     parsed.get("disclaimer","⚠️ Doctor confirm பண்ணுங்க."),
        }
        if mode == "medicine":
            sr.update({
                "uses":                parsed.get("uses",[]),
                "side_effects":        parsed.get("side_effects",[]),
                "warnings":            parsed.get("warnings",[]),
                "dosage":              parsed.get("dosage") or "Doctor prescription follow பண்ணுங்க",
                "medicine_identified": parsed.get("medicine_identified", True),
            })
        elif mode == "scan":
            sr.update({
                "body_part":         parsed.get("body_part",""),
                "scan_type":         parsed.get("scan_type",""),
                "implants_detected": parsed.get("implants_detected", False),
                "implant_details":   parsed.get("implant_details",""),
                "fractures_visible": parsed.get("fractures_visible", False),
            })
        elif mode == "lab":
            sr.update({
                "abnormal_findings": parsed.get("abnormal_findings",[]),
                "normal_findings":   parsed.get("normal_findings",[]),
            })

        return {
            "draft_answer":        answer,
            "pass1_answer":        answer,
            "pass2_fired":         False,
            "pass2_verified":      True,
            "pass3_fired":         False,
            "structured_response": sr,
            "detected_language":   lang,
            "model":               GROQ_MODEL,
            "latency_s":           round(time.time()-t0, 3),
        }
