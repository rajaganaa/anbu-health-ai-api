"""
engine/buddhi.py — Buddhi v2.3 FINAL
ROOT CAUSE FIX: Vision-extracted data now passed as ACTUAL REPORT DATA to LLM.
The LLM now sees real values (e.g. WBC: 5390, HbA1c: 6.5%) and explains them.
No more generic "doctor போங்க" for everything.
"""
import os, re, json, logging, time
from typing import Optional, Dict

logger = logging.getLogger(__name__)
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"  # hardcoded fallback — Azure strips empty env vars
TAMIL_RE   = re.compile(r'[\u0B80-\u0BFF]')

def detect_language(text: str) -> str:
    if len(TAMIL_RE.findall(text)) >= 3: return "ta"
    if re.search(r'\bin tamil\b|\btamil\b|\bதமிழ்\b', text, re.IGNORECASE): return "ta"
    return "en"

class GroqEngine:
    def __init__(self):
        from groq import Groq
        # Support multiple comma-separated keys for rotation when one hits its
        # rate limit (e.g. several free-tier Groq accounts) — falls back to
        # single-key behavior if only GROQ_API_KEY is set.
        primary_key = os.environ.get("GROQ_API_KEY", "")
        if not primary_key: raise RuntimeError("GROQ_API_KEY not set")
        extra_keys_raw = os.environ.get("GROQ_API_KEYS_EXTRA", "")
        extra_keys = [k.strip() for k in extra_keys_raw.split(",") if k.strip()]
        self.api_keys = [primary_key] + extra_keys
        self._key_index = 0
        self.client = Groq(api_key=self.api_keys[self._key_index])
        logger.info(f"[BUDDHI] Initialized with {len(self.api_keys)} Groq API key(s) available for rotation")
        # 3-model fallback chain: 70b (best) → 8b (fast, 500k/day) → gemma2 (backup)
        # Hardcoded defaults so Azure env var stripping never breaks this
        self.models = [
            os.environ.get("GROQ_MODEL")          or "llama-3.3-70b-versatile",
            os.environ.get("GROQ_MODEL_FALLBACK")  or "llama-3.1-8b-instant",
            os.environ.get("GROQ_MODEL_FALLBACK2") or "llama-3.1-70b-versatile",
        ]

    def _rotate_key(self):
        """Switch to the next API key in the pool, if any remain."""
        from groq import Groq
        if self._key_index < len(self.api_keys) - 1:
            self._key_index += 1
            self.client = Groq(api_key=self.api_keys[self._key_index])
            logger.warning(f"[BUDDHI] Rotated to Groq API key #{self._key_index + 1}/{len(self.api_keys)}")
            return True
        return False

    def chat(self, system, user, max_tokens=1400, temperature=0.1):
        import time
        last_err = None
        keys_tried_for_current_model = 0
        for i, model in enumerate(self.models):
            while True:
                try:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=[{"role":"system","content":system},{"role":"user","content":user}],
                        max_tokens=max_tokens, temperature=temperature,
                    )
                    if i > 0:
                        logger.info(f"[BUDDHI] Used fallback model #{i+1}: {model}")
                    return resp.choices[0].message.content.strip()
                except Exception as e:
                    err = str(e)
                    last_err = e
                    if "429" in err or "rate_limit" in err.lower():
                        # Try the next API key first (same model) before giving up on this model
                        if self._rotate_key():
                            time.sleep(0.3)
                            continue
                        logger.warning(f"[BUDDHI] Rate limit on {model} (all keys exhausted), trying next model...")
                        break
                    elif "model_not_found" in err.lower() or "does not exist" in err.lower() or "decommissioned" in err.lower() or "400" in err:
                        logger.warning(f"[BUDDHI] Model {model} not found, skipping...")
                        break
                    raise  # Other errors (auth, network) — raise immediately
            if i == len(self.models) - 1:
                # All models AND all keys exhausted — return minimal valid JSON
                logger.error(f"[BUDDHI] All models/keys rate-limited. Returning degraded response.")
                return '{"answer":"Rate limit reached. Please wait 1-2 minutes and try again. / சற்று நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.","summary":"API rate limit reached","confidence":0,"urgency":"low","findings":[],"recommendation":"Please try again in a few minutes","disclaimer":"⚠️ Doctor confirm பண்ணுங்க."}'
        return ""

_engine = None
def _get_engine():
    global _engine
    if _engine is None: _engine = GroqEngine()
    return _engine

# ── SYSTEM PROMPTS ─────────────────────────────────────────────────────────────

_LANGUAGE_RULES = """LANGUAGE RULES:
- Use clear simple Tamil for village patients
- Keep medical terms in English (Paracetamol, Glucose, MRI, etc.)
- DO NOT translate medical drug names or test names to Tamil
- DO NOT use literal word-for-word Tamil translation
- Use natural spoken Tamil like how a village doctor talks
- Example good: 'Paracetamol tablet தலைவலிக்கு நல்லது'
- Example bad: 'வலி நிவாரண மருந்து தலை வலி நீக்கும்'
- Disclaimer must say: இது educational மட்டும். Doctor கிட்ட போங்க."""

# Without this, the model defaults to re-explaining the WHOLE report/scan/
# medicine every turn (because the TASK instructions below are about
# explaining the document), even when the patient asked one narrow
# question ("what is the lab name?"). Shared across all modes so a narrow
# question gets a narrow, grounded answer regardless of mode.
_ANSWER_GROUNDING = """ANSWERING RULES — apply these to the "answer" field above all else:
- Read the "Patient question" given below FIRST. Your "answer" field's first sentence must directly answer THAT exact question — not a restatement of the whole report.
- If the question asks for one specific fact (a name, date, place, value, etc.) and that fact is present anywhere in the data given to you, state that exact value as your first sentence. Do not pad it with the general report summary unless the patient actually asked for an overview.
- If the question asks for a specific fact that is NOT present anywhere in the data given to you, say plainly that it wasn't found/extracted from the document. NEVER invent, guess, or assume a name, date, company, number, or any other fact that wasn't actually given to you above — this applies to the free-text "answer" field exactly as strictly as it applies to the structured fields."""

_LAB_SYSTEM = f"""You are Anbu Health AI for Tamil Nadu village patients.
You will receive ACTUAL LAB REPORT DATA extracted from the patient's report.
Use ONLY the provided data — never invent values.

RULES:
- Report EVERY test value provided with its status (High/Low/Normal)
- Group by: Diabetes, Cholesterol, Blood Count, Liver, Kidney
- Clearly mark abnormal values first
- Do NOT prescribe medicines
- Recommend doctor for abnormal values
- If patient_name/age/report_date/lab_name/doctor_name were provided in the data, copy them through EXACTLY as given — never invent or guess them. If not provided, leave as "".
- summary must be SPECIFIC: mention the actual abnormal test name(s) and value(s), not a generic line like "your report shows some issues". A summary with no real numbers/test names is a FAILURE.
- key_points must give 3-5 short, concrete, plain-language points that each explain ONE real finding from this report — what it is, whether it's high/low/normal, and why it matters for the patient. No vague filler like "consult your doctor" without context — context comes first, the doctor advice is separate.

{_LANGUAGE_RULES}

{_ANSWER_GROUNDING}

Return ONLY valid JSON (no text before/after):
{{
  "mode": "lab",
  "urgency": "low|medium|high",
  "confidence": 85,
  "patient_name": "from data or empty",
  "age": "from data or empty",
  "report_date": "from data or empty",
  "lab_name": "from data or empty",
  "doctor_name": "from data or empty",
  "summary": "2 sentence simple Tamil overview naming the actual abnormal test(s) and value(s)",
  "key_points": ["specific point 1 referencing a real value/finding", "specific point 2", "specific point 3"],
  "findings": ["TestName: value unit (HIGH/LOW/NORMAL — explain briefly)"],
  "abnormal_findings": ["only abnormal tests with values"],
  "normal_findings": ["only normal tests"],
  "recommendation": "Specific simple Tamil advice based on actual results",
  "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
  "answer": "3-4 sentence helpful explanation in natural spoken Tamil"
}}"""

_SCAN_SYSTEM = f"""You are Anbu Health AI for Tamil Nadu village patients.
You will receive data from a medical scan or X-ray analysis.

CRITICAL RULES:
- If surgical hardware (plates/screws/rods) detected → explicitly state post-surgical status
- NEVER prescribe medicines from X-rays
- NEVER invent dosage
- If uncertain → say "Doctor review தேவை"
- If patient_name/age/scan_date/scan_provider/doctor_name were provided in the data, copy them through EXACTLY as given — never invent or guess them. If not provided, leave as "".
- summary must be SPECIFIC: name the actual body part and the actual finding(s), not a generic line like "scan looks fine". A summary with no real findings is a FAILURE.
- key_points must give 3-5 short, concrete, plain-language points that each explain ONE real finding from this scan — what it is and why it matters. No vague filler.

{_LANGUAGE_RULES}

{_ANSWER_GROUNDING}

Return ONLY valid JSON:
{{
  "mode": "scan",
  "urgency": "low|medium|high",
  "confidence": 70,
  "body_part": "identified body part",
  "scan_type": "X-ray|CT|MRI|Ultrasound",
  "patient_name": "from data or empty",
  "age": "from data or empty",
  "scan_date": "from data or empty",
  "scan_provider": "from data or empty",
  "doctor_name": "from data or empty",
  "summary": "Simple Tamil summary naming the actual body part and finding(s)",
  "key_points": ["specific point 1 referencing a real finding", "specific point 2", "specific point 3"],
  "findings": ["clear finding 1", "clear finding 2"],
  "implants_detected": false,
  "implant_details": "describe hardware or null",
  "fractures_visible": false,
  "recommendation": "Next step — NO medicine/dosage",
  "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
  "answer": "3-4 sentence explanation for patient in natural spoken Tamil"
}}"""

_MEDICINE_SYSTEM = f"""You are Anbu Health AI for Tamil Nadu village patients.
You will receive medicine identification data.

RULES:
- Give REAL medicine information — uses, side effects, warnings
- NEVER invent dosage numbers or expiry/mfg dates — only pass through what was actually extracted from the packaging
- If dosage_instructions/exp_date/mfg_date were provided in the data, copy them through EXACTLY as given. If not provided, leave as "".
- Always say doctor prescription required for the exact frequency/quantity to take
- summary must be SPECIFIC: name the actual drug and its actual category/use, not a generic line like "this is a tablet". A summary with no real drug info is a FAILURE.
- key_points must give 3-5 short, concrete, plain-language points — what the drug is for, one key warning, one storage/expiry note, etc. No vague filler.

{_LANGUAGE_RULES}

{_ANSWER_GROUNDING}

Return ONLY valid JSON:
{{
  "mode": "medicine",
  "urgency": "low",
  "confidence": 85,
  "medicine_identified": true,
  "drug_name": "medicine name",
  "drug_category": "antacid/antibiotic/painkiller/etc",
  "exp_date": "from data or empty",
  "mfg_date": "from data or empty",
  "dosage": "dosage_instructions from data if present, else null",
  "summary": "Simple Tamil — what this medicine is, naming the actual drug",
  "key_points": ["specific point 1", "specific point 2", "specific point 3"],
  "uses": ["specific use 1", "specific use 2", "specific use 3"],
  "side_effects": ["effect 1", "effect 2", "effect 3"],
  "warnings": ["warning 1", "warning 2"],
  "recommendation": "Doctor prescription follow பண்ணுங்க",
  "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
  "answer": "3-4 sentence explanation about this medicine in natural spoken Tamil"
}}"""

_GENERAL_SYSTEM = f"""You are Anbu Health AI for Tamil Nadu village patients.
Give helpful, accurate medical information. Simple language. Never diagnose from symptoms alone.

{_LANGUAGE_RULES}

{_ANSWER_GROUNDING}

Return ONLY valid JSON:
{{
  "mode": "general",
  "urgency": "low|medium|high",
  "confidence": 80,
  "summary": "Direct answer",
  "key_points": ["specific point 1", "specific point 2", "specific point 3"],
  "details": ["specific point 1", "specific point 2", "specific point 3"],
  "recommendation": "What patient should do",
  "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
  "answer": "3-4 sentence helpful answer with actual medical info in natural spoken Tamil"
}}"""

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
        "key_points":gl("key_points"),
        "uses":gl("uses"),"side_effects":gl("side_effects"),"warnings":gl("warnings"),
        "recommendation":g("recommendation") or "Doctor கிட்ட போங்க.",
        "disclaimer":g("disclaimer") or "⚠️ Doctor confirm பண்ணுங்க.",
        "answer":ans,"implants_detected":False,"fractures_visible":False,
        "body_part":g("body_part"),"scan_type":g("scan_type"),
        "drug_name":g("drug_name"),"dosage":None,"medicine_identified":True,
        "abnormal_findings":gl("abnormal_findings"),"normal_findings":gl("normal_findings"),
        "patient_name":g("patient_name"),"age":g("age"),"report_date":g("report_date"),
        "lab_name":g("lab_name"),"doctor_name":g("doctor_name"),
        "scan_date":g("scan_date"),"scan_provider":g("scan_provider"),
        "exp_date":g("exp_date"),"mfg_date":g("mfg_date"),
    }

def _fallback(mode: str) -> Dict:
    return {
        "mode":mode,"urgency":"low","confidence":0,
        "summary":"Analysis மீண்டும் try பண்ணுங்க.",
        "findings":[],"details":[],"key_points":[],"uses":[],"side_effects":[],"warnings":[],
        "abnormal_findings":[],"normal_findings":[],
        "recommendation":"Doctor கிட்ட போங்க.",
        "disclaimer":"⚠️ Doctor confirm பண்ணுங்க.",
        "answer":"Sorry, analysis fail ஆச்சு. மீண்டும் try பண்ணுங்க.",
    }

# ── Vision Context Builder ───────────────────────────────────────────────────

def _unwrap_vault(vision_info: Dict) -> Dict:
    """Safety-net: if vision_info is a {filename: {data}} vault dict that
    somehow wasn't unwrapped by main.py, flatten it into a single merged dict.
    A genuine vision result always has at least one of these keys at the top
    level; a vault dict has filename strings as keys instead.
    """
    _vision_keys = {
        "tests", "findings", "drug_name", "brand_name", "summary", "mode",
        "scan_type", "body_part", "lab_name", "patient_name", "error",
        "raw_text", "model", "overall_status", "abnormal_count",
    }
    if not vision_info:
        return vision_info
    # If ANY top-level key is a known vision field, it's already flat
    if any(k in _vision_keys for k in vision_info):
        return vision_info
    # Otherwise it looks like a vault: values are dicts (the actual vision data)
    entries = [v for v in vision_info.values() if isinstance(v, dict) and not v.get("error")]
    if not entries:
        return vision_info
    merged = dict(entries[0])
    for entry in entries[1:]:
        for lk in ("tests", "findings", "abnormalities"):
            if entry.get(lk) and isinstance(merged.get(lk), list):
                merged[lk] = merged[lk] + entry[lk]
        for sk in (
            "patient_name", "age", "gender", "lab_name", "doctor_name",
            "report_date", "scan_date", "scan_provider", "drug_name",
            "brand_name", "manufacturer", "expiry", "mfg_date",
            "dosage_instructions", "summary", "overall_status",
            "scan_type", "body_part", "impression",
        ):
            if not merged.get(sk) and entry.get(sk):
                merged[sk] = entry[sk]
    return merged


def _build_vision_context(vision_info: Dict, mode: str) -> str:
    """Convert vision output into detailed context for LLM."""
    if not vision_info or vision_info.get("error"): return ""

    # Safety-net: unwrap vault format if main.py didn't do it already
    vision_info = _unwrap_vault(vision_info)
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
        # Patient / report header — pass through verbatim, LLM should copy not invent
        header = [(k, vision_info.get(k,"")) for k in
                  ("patient_name","age","gender","report_date","lab_name","doctor_name")]
        header = [(k,v) for k,v in header if v]
        if header:
            lines.append("=== REPORT HEADER (copy these EXACTLY into the matching output fields) ===")
            for k,v in header:
                lines.append(f"  {k}: {v}")
        # Always include raw summary/text — handles scrambled Indian lab PDFs
        summary = vision_info.get("summary","")
        raw_text = vision_info.get("raw_text","")
        if summary: lines.append(f"Summary: {summary}")
        if raw_text and not tests:
            lines.append("=== RAW LAB REPORT TEXT (parse carefully) ===")
            lines.append(raw_text[:3000])
        lines.append("TASK: Extract ALL test values, compare to reference ranges, explain abnormal values in simple Tanglish. Write a SPECIFIC summary (name the actual abnormal test/value) and 3-5 specific key_points — no generic filler.")

    elif mode == "scan":
        lines.append("=== SCAN/X-RAY ANALYSIS ===")
        for k in ("scan_type", "body_part", "impression"):
            v = vision_info.get(k, "")
            if v: lines.append(f"  {k}: {v}")
        findings = vision_info.get("findings", [])
        if findings: lines.append(f"  Findings: {', '.join(str(f) for f in findings)}")
        abnorm = vision_info.get("abnormalities", [])
        if abnorm: lines.append(f"  Abnormalities: {', '.join(str(a) for a in abnorm)}")
        # Also surface summary + raw_text if no structured findings were extracted
        summary = vision_info.get("summary", "")
        if summary: lines.append(f"  Summary: {summary}")
        raw_text = vision_info.get("raw_text", "")
        if raw_text and not findings:
            lines.append("=== RAW SCAN REPORT TEXT (parse carefully) ===")
            lines.append(raw_text[:3000])
        # Patient / scan header — pass through verbatim, LLM should copy not invent
        header = [(k, vision_info.get(k, "")) for k in
                  ("patient_name", "age", "gender", "scan_date", "scan_provider", "doctor_name")]
        header = [(k, v) for k, v in header if v]
        if header:
            lines.append("=== SCAN HEADER (copy these EXACTLY into the matching output fields) ===")
            for k, v in header:
                lines.append(f"  {k}: {v}")
        lines.append("TASK: Explain what these findings mean for the patient. If hardware visible, mention post-surgical status. Write a SPECIFIC summary (name the actual body part/finding) and 3-5 specific key_points — no generic filler.")


    elif mode == "medicine":
        lines.append("=== MEDICINE IDENTIFIED ===")
        for k in ("drug_name","brand_name","generic_name","strength","form","drug_category","manufacturer"):
            v = vision_info.get(k,"")
            if v and v not in ("Not visible","Not detected",""): lines.append(f"  {k}: {v}")
        # Mfg/Exp/dosage printed on packaging — pass through verbatim, LLM should copy not invent
        for k in ("mfg_date","expiry","dosage_instructions"):
            v = vision_info.get(k,"")
            if v and v not in ("Not visible","Not detected",""): lines.append(f"  {k}: {v}")
        s = vision_info.get("summary","")
        if s: lines.append(f"  Summary: {s}")

        # Tool results — expiry check, FDA adverse events, drug interactions
        tool_results = vision_info.get("tool_results", {})
        if tool_results.get("expiry"):
            exp = tool_results["expiry"]
            lines.append("=== EXPIRY CHECK ===")
            lines.append(f"  Status: {exp.get('status')} — {exp.get('message')}")
        if tool_results.get("fda") and tool_results["fda"].get("found"):
            fda = tool_results["fda"]
            lines.append("=== FDA ADVERSE EVENTS (Top reactions) ===")
            for r in fda.get("reactions", [])[:5]:
                lines.append(f"  - {r['reaction']}: {r['reports']:,} reports")
        if tool_results.get("interactions"):
            lines.append("=== DRUG INTERACTIONS ===")
            for ix in tool_results["interactions"]:
                lines.append(f"  [{ix['level']}] {ix['drug1']} + {ix['drug2']}: {ix['effect']}")

        lines.append("TASK: Explain this medicine — uses, side effects, warnings in Tanglish. Write a SPECIFIC summary (name the actual drug) and 3-5 specific key_points — no generic filler.")

    if not lines:
        s = vision_info.get("summary","")
        if s: lines.append(f"Context: {s}")

    return "\n".join(lines) + "\n\n" if lines else ""

# ── Deterministic field lookup ──────────────────────────────────────────────
# For a narrow question like "what is the lab name?", the most reliable
# answer is a direct key lookup against the already-extracted data — not
# another LLM generation that may (as observed in production) ignore the
# specific question and re-explain the whole report instead.
# Used as a fast-path BEFORE calling Groq; if no lookup phrase matches,
# normal LLM reasoning proceeds unchanged.
#
# (phrase_to_match_lowercase, vision_info_key, display_label)
_LOOKUP_FIELDS = {
    "lab": [
        ("patient name",    "patient_name", "Patient name"),
        ("lab name",        "lab_name",     "Lab name"),
        ("laboratory name", "lab_name",     "Lab name"),
        ("diagnostic cent", "lab_name",     "Diagnostic centre"),
        ("doctor name",     "doctor_name",  "Doctor name"),
        ("report date",     "report_date",  "Report date"),
        ("patient age",     "age",          "Age"),
    ],
    "scan": [
        ("patient name",  "patient_name",   "Patient name"),
        ("scan cent",     "scan_provider",  "Scan centre"),
        ("imaging cent",  "scan_provider",  "Scan centre"),
        ("hospital name", "scan_provider",  "Scan centre"),
        ("scan date",     "scan_date",      "Scan date"),
        ("doctor name",   "doctor_name",    "Doctor name"),
        ("patient age",   "age",            "Age"),
    ],
    "medicine": [
        ("manufacturer",        "manufacturer",   "Manufacturer"),
        ("who makes",           "manufacturer",   "Manufacturer"),
        ("made by",             "manufacturer",   "Manufacturer"),
        ("which company",       "manufacturer",   "Manufacturer"),
        ("batch number",        "batch_number",   "Batch number"),
        ("batch no",            "batch_number",   "Batch number"),
        ("manufacturing date",  "mfg_date",       "Manufacturing date"),
        ("mfg date",            "mfg_date",       "Manufacturing date"),
        ("expiry date",         "expiry",         "Expiry date"),
        ("expiration date",     "expiry",         "Expiry date"),
        ("medicine name",       "drug_name",      "Medicine name"),
        ("drug name",           "drug_name",      "Medicine name"),
    ],
}
# Tamil equivalents — merged in at lookup time
_LOOKUP_FIELDS_TA = {
    "lab": [
        ("பேஷன்ட் பெயர்", "patient_name", "Patient name"),
        ("நோயாளி பெயர்",   "patient_name", "Patient name"),
        ("லேப் பெயர்",      "lab_name",     "Lab name"),
        ("ஆய்வக",           "lab_name",     "Lab name"),
        ("டாக்டர் பெயர்",   "doctor_name",  "Doctor name"),
        ("அறிக்கை தேதி",    "report_date",  "Report date"),
        ("வயது",             "age",          "Age"),
    ],
    "scan": [
        ("பேஷன்ட் பெயர்",   "patient_name",  "Patient name"),
        ("ஸ்கேன் மையம்",    "scan_provider", "Scan centre"),
        ("ஸ்கேன் தேதி",     "scan_date",     "Scan date"),
        ("டாக்டர் பெயர்",   "doctor_name",   "Doctor name"),
    ],
    "medicine": [
        ("தயாரிப்பாளர்",    "manufacturer", "Manufacturer"),
        ("எந்த நிறுவனம்",   "manufacturer", "Manufacturer"),
        ("காலாவதி",          "expiry",       "Expiry date"),
        ("மருந்தின் பெயர்", "drug_name",    "Medicine name"),
    ],
}


def _try_direct_lookup(question: str, vision_info: Dict, mode: str) -> Optional[Dict]:
    """Return a ready-made parsed-style dict if the question is a direct
    field lookup this mode supports, else None (falls through to normal LLM
    reasoning). If the phrase matches but the field wasn't actually
    extracted, answers honestly that it wasn't found instead of letting
    the model guess."""
    if not vision_info or vision_info.get("error") or mode not in _LOOKUP_FIELDS:
        return None
    q = question.lower()
    candidates = _LOOKUP_FIELDS.get(mode, []) + _LOOKUP_FIELDS_TA.get(mode, [])
    for phrase, field_key, label in candidates:
        if phrase.lower() in q:
            value = (vision_info.get(field_key) or "").strip()
            is_ta = bool(TAMIL_RE.search(question))
            if value and value not in ("Not visible", "Not detected", "Unknown", ""):
                answer = (f"{label}: {value}" if not is_ta
                          else f"{label} — {value}.")
            else:
                answer = (f"{label} wasn't extracted from this document — "
                          f"it may not be printed on it, or wasn't readable in the photo."
                          if not is_ta else
                          f"இந்த document-ல {label} extract ஆகவில்லை — "
                          f"அது அதில் இல்லாமல் இருக்கலாம் அல்லது படத்தில் தெளிவாக இல்லாமல் இருக்கலாம்.")
            logger.info(f"[BUDDHI] Direct lookup matched: phrase='{phrase}' field={field_key} found={bool(value)}")
            return {
                "mode": mode, "urgency": "low", "confidence": 95 if value else 60,
                "summary": answer, "answer": answer,
                "key_points": [], "findings": [], "details": [],
                "recommendation": "Doctor கிட்ட confirm பண்ணுங்க.",
                "disclaimer": "⚠️ இது educational மட்டும். Doctor கிட்ட போங்க.",
                "patient_name": vision_info.get("patient_name", ""),
                "age": vision_info.get("age", ""), "report_date": vision_info.get("report_date", ""),
                "lab_name": vision_info.get("lab_name", ""), "doctor_name": vision_info.get("doctor_name", ""),
                "scan_date": vision_info.get("scan_date", ""), "scan_provider": vision_info.get("scan_provider", ""),
                "drug_name": vision_info.get("drug_name", ""),
            }
    return None


def _assemble_result(parsed: Dict, mode: str, vi: Dict, lang: str, model_label: str, t0: float) -> Dict:
    """Shared by both the direct-lookup fast-path and the normal LLM path —
    builds the same structured_response shape either way, so the frontend
    UI cards render identically regardless of which path answered."""
    answer     = parsed.get("answer") or parsed.get("summary") or "மீண்டும் try பண்ணுங்க."
    confidence = int(str(parsed.get("confidence", "70")).replace("%", "")) if parsed.get("confidence") else 70

    def pick(*vals):
        """First non-empty, non-placeholder value — prefers vision-extracted (OCR) data over the LLM echo."""
        for v in vals:
            if v and v not in ("Not visible", "Not detected"):
                return v
        return ""

    sr = {
        "summary":        parsed.get("summary", ""),
        "full_answer":    answer,
        "findings":       parsed.get("findings") or parsed.get("details") or [],
        "key_points":     parsed.get("key_points") or [],
        "recommendation": parsed.get("recommendation", ""),
        "urgency":        parsed.get("urgency", "low"),
        "confidence":     confidence,
        "disclaimer":     parsed.get("disclaimer", "⚠️ Doctor confirm பண்ணுங்க."),
    }
    if mode == "medicine":
        tool_results = vi.get("tool_results", {})
        sr.update({
            "uses":                parsed.get("uses", []),
            "side_effects":        parsed.get("side_effects", []),
            "warnings":            parsed.get("warnings", []),
            "dosage":              pick(vi.get("dosage_instructions"), parsed.get("dosage")) or "Doctor prescription follow பண்ணுங்க",
            "medicine_identified": parsed.get("medicine_identified", True),
            "exp_date":            pick(vi.get("expiry"), parsed.get("exp_date")),
            "mfg_date":            pick(vi.get("mfg_date"), parsed.get("mfg_date")),
            "expiry_status":       tool_results.get("expiry") or {},
        })
    elif mode == "scan":
        sr.update({
            "body_part":         parsed.get("body_part", ""),
            "scan_type":         parsed.get("scan_type", ""),
            "implants_detected": parsed.get("implants_detected", False),
            "implant_details":   parsed.get("implant_details", ""),
            "fractures_visible": parsed.get("fractures_visible", False),
            "patient_name":      pick(vi.get("patient_name"), parsed.get("patient_name")),
            "age":               pick(vi.get("age"), parsed.get("age")),
            "scan_date":         pick(vi.get("scan_date"), parsed.get("scan_date")),
            "scan_provider":     pick(vi.get("scan_provider"), parsed.get("scan_provider")),
            "doctor_name":       pick(vi.get("doctor_name"), parsed.get("doctor_name")),
        })
    elif mode == "lab":
        sr.update({
            "abnormal_findings": parsed.get("abnormal_findings", []),
            "normal_findings":   parsed.get("normal_findings", []),
            "patient_name":      pick(vi.get("patient_name"), parsed.get("patient_name")),
            "age":               pick(vi.get("age"), parsed.get("age")),
            "report_date":       pick(vi.get("report_date"), parsed.get("report_date")),
            "lab_name":          pick(vi.get("lab_name"), parsed.get("lab_name")),
            "doctor_name":       pick(vi.get("doctor_name"), parsed.get("doctor_name")),
        })

    return {
        "draft_answer":        answer,
        "pass1_answer":        answer,
        "pass2_fired":         False,
        "pass2_verified":      True,
        "pass3_fired":         False,
        "structured_response": sr,
        "detected_language":   lang,
        "model":               model_label,
        "latency_s":           round(time.time() - t0, 3),
    }


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

        # Fast-path: a direct lookup of an already-extracted field beats an
        # LLM regeneration that — as seen in production — sometimes ignores
        # the specific question and re-explains the whole document instead.
        # Skips the Groq call entirely when it matches: faster, free, and
        # the answer is guaranteed to match what was actually extracted.
        direct = _try_direct_lookup(question, vision_info or {}, mode)
        if direct is not None:
            return _assemble_result(direct, mode, vision_info or {}, lang, "direct_lookup", t0)

        vision_ctx = _build_vision_context(vision_info or {}, mode)
        rag_ctx    = f"Medical Reference:\n{context_str}\n\n" if context_str.strip() else ""
        lang_note  = "\nIMPORTANT: Follow the LANGUAGE RULES — natural spoken Tamil, English medical terms." if lang == "ta" else "\nFollow the LANGUAGE RULES — natural spoken Tamil, English medical terms."

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

        parsed = _parse_json(raw, mode)
        return _assemble_result(parsed, mode, vision_info or {}, lang, GROQ_MODEL, t0)
