"""
vision/anbu_vision.py — Vision + PDF Analyzer v2.4
Fix: GITHUB_TOKEN read fresh on every call (not frozen at module load).
"""
import os, base64, logging, re, json
from typing import Dict
from pathlib import Path

logger = logging.getLogger(__name__)

ENDPOINT = "https://models.inference.ai.azure.com"
MODEL    = "gpt-4o"

PROMPTS = {
    "medicine": """You are a pharmacist AI. Analyze this medicine image and extract:
1. Brand name (exact text on strip/box)
2. Generic name (scientific name)
3. Strength (e.g., 500mg, 40mg)
4. Form (tablet/capsule/syrup/injection)
5. Drug category (antibiotic/painkiller/antacid/etc)

Respond ONLY in this exact JSON format (no extra text):
{
  "brand_name": "...",
  "generic_name": "...",
  "drug_name": "...",
  "strength": "...",
  "form": "...",
  "manufacturer": "...",
  "drug_category": "...",
  "summary": "one sentence about this medicine"
}
If any field is not visible, use "Not visible".""",

    "lab": """You are a lab technician AI. Analyze this lab report and extract ALL test results.

Respond ONLY in this exact JSON format:
{
  "tests": [{"name": "...", "value": "...", "unit": "...", "range": "...", "status": "normal/high/low"}],
  "abnormal_count": 0,
  "summary": "one sentence summary of key findings",
  "overall_status": "normal/attention/urgent"
}""",

    "scan": """You are a radiologist AI. Analyze this medical scan/X-ray.
IMPORTANT: If you see metal plates, screws, or rods — explicitly say "post-surgical hardware detected".

Respond ONLY in this exact JSON format:
{
  "scan_type": "X-ray/CT/MRI/Ultrasound",
  "body_part": "...",
  "findings": ["finding1", "finding2"],
  "abnormalities": ["abnormality1"],
  "impression": "...",
  "urgency": "low/medium/high",
  "summary": "one sentence overall summary"
}""",
}


def _get_github_token() -> str:
    """Read token fresh every call — never frozen at import time."""
    return (
        os.environ.get("VISION_GITHUB_TOKEN") or
        os.environ.get("GITHUB_TOKEN") or ""
    )


def _extract_pdf_text(pdf_path: str) -> str:
    """Extract text from PDF — tries 3 methods."""
    text = ""

    # Method 1: PyMuPDF (fitz) — most reliable
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        if text.strip():
            logger.info(f"[VISION] PyMuPDF extracted {len(text)} chars")
            return text
    except Exception as e:
        logger.warning(f"[VISION] PyMuPDF failed: {e}")

    # Method 2: pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if text.strip():
            logger.info(f"[VISION] pdfplumber extracted {len(text)} chars")
            return text
    except Exception as e:
        logger.warning(f"[VISION] pdfplumber failed: {e}")

    # Method 3: pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            logger.info(f"[VISION] pypdf extracted {len(text)} chars")
            return text
    except Exception as e:
        logger.warning(f"[VISION] pypdf failed: {e}")

    logger.error(f"[VISION] ALL PDF extractors failed for {pdf_path}")
    return ""


def _parse_json(raw: str) -> Dict:
    clean = re.sub(r'```(?:json)?\s*', '', raw).strip().strip('`').strip()
    try:
        return json.loads(clean)
    except Exception:
        pass
    start, end = clean.find('{'), clean.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(clean[start:end+1])
        except Exception:
            pass
    return {"raw_response": raw, "parse_error": True}


def _analyze_pdf_with_groq(text: str, mode: str) -> Dict:
    """Use Groq to analyze extracted PDF text."""
    from groq import Groq
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return {"error": "No API key", "summary": text[:300], "mode": mode}

    client = Groq(api_key=api_key)
    model  = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    if mode == "lab":
        system = """Extract ALL lab test results from this text.
Return ONLY JSON:
{
  "tests": [{"name": "TestName", "value": "123", "unit": "mg/dL", "range": "70-100", "status": "normal/high/low"}],
  "abnormal_count": 0,
  "summary": "2 sentence summary of key abnormal findings",
  "overall_status": "normal/attention/urgent"
}
Extract every single test value you find. Be thorough."""
    else:
        system = f"""Analyze this medical {mode} report text.
Return ONLY JSON:
{{
  "summary": "2 sentence summary of key findings",
  "findings": ["finding1", "finding2"],
  "overall_status": "normal/attention/urgent"
}}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": f"Report text:\n{text[:4000]}"},
            ],
            max_tokens=1000, temperature=0.0,
        )
        raw    = resp.choices[0].message.content.strip()
        result = _parse_json(raw)
        result["mode"]  = mode
        result["model"] = "groq-text"
        logger.info(f"[VISION] PDF analyzed: {result.get('summary','')[:80]}")
        return result
    except Exception as e:
        logger.error(f"[VISION] Groq text analysis failed: {e}")
        return {"error": str(e), "summary": text[:300], "mode": mode}


def _fallback_result(mode: str, reason: str = "") -> Dict:
    base = {"mode": mode, "model": "fallback", "error": reason or "Analysis unavailable"}
    if mode == "medicine":
        base.update({"brand_name": "Not detected", "generic_name": "Not detected",
                     "drug_name": "Unknown", "summary": "Could not read medicine"})
    elif mode == "lab":
        base.update({"tests": [], "abnormal_count": 0,
                     "summary": "Could not extract lab values", "overall_status": "unknown"})
    else:
        base.update({"scan_type": "Unknown", "body_part": "Unknown",
                     "findings": [], "summary": "Could not analyze scan"})
    return base


def analyze_image(image_path: str, mode: str = "medicine") -> Dict:
    """Main entry point — analyze image or PDF."""
    ext = Path(image_path).suffix.lower().lstrip('.')

    # ── PDF ───────────────────────────────────────────────────────────────────
    if ext == "pdf":
        text = _extract_pdf_text(image_path)
        if not text.strip():
            logger.error(f"[VISION] PDF extraction returned empty text: {image_path}")
            return _fallback_result(mode, "PDF text extraction failed")
        logger.info(f"[VISION] PDF text length: {len(text)}")
        return _analyze_pdf_with_groq(text, mode)

    # ── Image ─────────────────────────────────────────────────────────────────
    # Read token FRESH — not from module-level constant
    github_token = _get_github_token()
    if not github_token:
        logger.warning("[VISION] VISION_GITHUB_TOKEN not set — vision unavailable")
        return _fallback_result(mode, "Vision token not configured")

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        media_type = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png",  "webp": "image/webp",
        }.get(ext, "image/jpeg")

        from openai import OpenAI
        # Use fresh token every call
        client = OpenAI(base_url=ENDPOINT, api_key=github_token)
        prompt = PROMPTS.get(mode, PROMPTS["medicine"])

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{media_type};base64,{image_data}"
                    }},
                ],
            }],
            max_tokens=800, temperature=0.0,
        )

        raw    = response.choices[0].message.content.strip()
        result = _parse_json(raw)
        result["mode"]  = mode
        result["model"] = "gpt-4o"
        logger.info(f"[VISION] Image analyzed: {result.get('summary','')[:80]}")
        return result

    except Exception as e:
        logger.error(f"[VISION] Image analysis failed: {e}")
        return _fallback_result(mode, str(e))
