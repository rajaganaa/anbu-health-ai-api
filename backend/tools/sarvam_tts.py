"""
tools/sarvam_tts.py — Sarvam AI Bulbul TTS integration

Generates natural-sounding Tamil/Indic speech for the "Listen" feature,
replacing the browser's robotic built-in speechSynthesis voice.

Docs: https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert
No SDK dependency — pure requests, consistent with tools/medical_tools.py.
"""

import os
import base64
import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

SARVAM_API_KEY      = os.getenv("SARVAM_API_KEY", "")
SARVAM_TTS_URL       = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_MODEL     = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
SARVAM_TTS_SPEAKER   = os.getenv("SARVAM_TTS_SPEAKER", "pooja")   # natural Indian female voice
SARVAM_TTS_PACE      = float(os.getenv("SARVAM_TTS_PACE", "0.95"))
SARVAM_TTS_SAMPLE_RATE = int(os.getenv("SARVAM_TTS_SAMPLE_RATE", "22050"))

MAX_CHARS = 2000  # bulbul:v3 hard limit is 2500 — leave headroom


def synthesize_speech(text: str, language_code: str = "ta-IN") -> Tuple[Optional[bytes], str]:
    """
    Convert text to natural speech via Sarvam AI Bulbul TTS.

    Returns:
        (audio_bytes, audio_format) on success — audio_format is always "wav".
        (None, "") if SARVAM_API_KEY is missing, text is empty, or the call fails.
    """
    if not SARVAM_API_KEY:
        logger.warning("SARVAM_API_KEY not set — TTS unavailable")
        return None, ""
    text = (text or "").strip()
    if not text:
        return None, ""
    text = text[:MAX_CHARS]

    payload = {
        "text": text,
        "target_language_code": language_code,
        "model": SARVAM_TTS_MODEL,
        "speaker": SARVAM_TTS_SPEAKER,
        "pace": SARVAM_TTS_PACE,
        "speech_sample_rate": SARVAM_TTS_SAMPLE_RATE,
    }
    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        audios = data.get("audios", [])
        if not audios:
            logger.error(f"Sarvam TTS returned no audio data: {data}")
            return None, ""
        audio_bytes = base64.b64decode("".join(audios))
        return audio_bytes, "wav"
    except requests.exceptions.HTTPError as e:
        body = getattr(e.response, "text", "")[:300]
        logger.error(f"Sarvam TTS HTTP {getattr(e.response,'status_code','?')}: {body}")
        return None, ""
    except Exception as e:
        logger.error(f"Sarvam TTS request failed: {e}")
        return None, ""
