"""
vision/anbu_vision.py — GPT-4o Vision Analyzer
Anbu Health AI v2.0

Modes:
  medicine → extracts drug name, strength, dosage, expiry
  lab      → extracts test names and values
  scan     → describes X-ray/MRI/ultrasound findings
"""
import os
import base64
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ENDPOINT = "https://models.inference.ai.azure.com"
MODEL = "gpt-4o"

PROMPTS = {
    "medicine": """You are a pharmacist AI. Analyze this medicine image and extract:
1. Brand name (exact text on strip/box)
2. Generic name (scientific name)
3. Strength (e.g., 500mg, 40mg)
4. Form (tablet/capsule/syrup/injection)
5. Manufacturer name
6. Expiry date (if visible)
7. Drug category (antibiotic/painkiller/antacid/etc)

Respond ONLY in this exact JSON format:
{
  "brand_name": "...",
  "generic_name": "...",
  "drug_name": "...",
  "strength": "...",
  "form": "...",
  "manufacturer": "...",
  "expiry_date": "...",
  "drug_category": "...",
  "summary": "one sentence about this medicine"
}
If any field is not visible, use "Not visible".""",

    "lab": """You are a lab technician AI. Analyze this lab report image and extract:
1. Test names and their values
2. Reference ranges if visible
3. Which values are abnormal (high/low)
4. Overall report summary

Respond ONLY in this exact JSON format:
{
  "tests": [{"name": "...", "value": "...", "unit": "...", "range": "...", "status": "normal/high/low"}],
  "abnormal_count": 0,
  "summary": "one sentence summary of findings",
  "overall_status": "normal/attention/urgent"
}""",

    "scan": """You are a radiologist AI. Analyze this medical scan/X-ray image and describe:
1. Type of scan (X-ray/CT/MRI/Ultrasound)
2. Body part visible
3. Key findings (what you see)
4. Any abnormalities
5. Overall impression

Respond ONLY in this exact JSON format:
{
  "scan_type": "...",
  "body_part": "...",
  "findings": ["finding1", "finding2"],
  "abnormalities": ["abnormality1"],
  "impression": "...",
  "urgency": "low/medium/high",
  "summary": "one sentence overall summary"
}""",
}

def analyze_image(image_path: str, mode: str = "medicine") -> Dict:
    """Analyze image using GPT-4o Vision."""
    if not GITHUB_TOKEN:
        logger.warning("[VISION] GITHUB_TOKEN not set — using fallback")
        return _fallback_result(mode)

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        ext = image_path.split(".")[-1].lower()
        media_type = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp",
        }.get(ext, "image/jpeg")

        from openai import OpenAI
        client = OpenAI(
            base_url=ENDPOINT,
            api_key=GITHUB_TOKEN,
        )

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
            max_tokens=800,
            temperature=0.0,
        )

        raw = response.choices[0].message.content.strip()
        logger.info(f"[VISION] Raw response: {raw[:100]}")

        result = _parse_json(raw)
        result["mode"] = mode
        result["model"] = "gpt-4o"
        return result

    except Exception as e:
        logger.error(f"[VISION] Failed: {e}")
        return {"error": str(e), "mode": mode, "model": "gpt-4o"}


def _parse_json(raw: str) -> Dict:
    """Parse JSON from GPT-4o response."""
    import json
    # Remove markdown code blocks
    clean = re.sub(r'```(?:json)?\n?', '', raw).strip().rstrip('`').strip()
    try:
        return json.loads(clean)
    except Exception:
        # Try to extract JSON object
        m = re.search(r'\{.*\}', clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw_response": raw, "parse_error": True}


def _fallback_result(mode: str) -> Dict:
    """Return empty structure when vision unavailable."""
    if mode == "medicine":
        return {
            "brand_name": "Not detected",
            "generic_name": "Not detected",
            "drug_name": "Unknown",
            "strength": "Not visible",
            "form": "Not visible",
            "manufacturer": "Not visible",
            "expiry_date": "Not visible",
            "drug_category": "Unknown",
            "summary": "Vision AI unavailable — GITHUB_TOKEN not set",
            "mode": mode,
            "model": "fallback",
        }
    elif mode == "lab":
        return {
            "tests": [],
            "abnormal_count": 0,
            "summary": "Vision AI unavailable",
            "overall_status": "unknown",
            "mode": mode,
            "model": "fallback",
        }
    else:
        return {
            "scan_type": "Unknown",
            "body_part": "Unknown",
            "findings": [],
            "abnormalities": [],
            "impression": "Vision AI unavailable",
            "urgency": "low",
            "summary": "Vision AI unavailable — GITHUB_TOKEN not set",
            "mode": mode,
            "model": "fallback",
        }
