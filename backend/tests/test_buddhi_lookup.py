"""
tests/test_buddhi_lookup.py — unit tests for the direct field-lookup fix.

Background: narrow questions like "what is the lab name?" were being
answered with a generic, repeated report summary instead of the actual
requested field — even though the field was correctly extracted and sitting
right there in vision_info. These tests prove the deterministic lookup
fast-path in engine/buddhi.py answers these correctly, and is honest (says
"not found") rather than guessing when a field truly wasn't extracted.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.buddhi import _try_direct_lookup, _assemble_result


LAB_VISION = {
    "mode": "lab", "patient_name": "Mr. JEGAJEEVAN RAM", "age": "50 Y",
    "report_date": "25/01/2026", "lab_name": "VDC", "doctor_name": "",
    "tests": [{"name": "HbA1c", "value": "6.5", "unit": "%", "range": "4-5.6", "status": "high"}],
    "summary": "உங்கள் ரத்த சர்க்கரை அளவு 151.9 mg/dl ... அதிகமாக உள்ளது.",
}

SCAN_VISION = {
    "mode": "scan", "scan_provider": "METROPOLIS", "scan_date": "12/03/2024",
    "body_part": "elbow", "scan_type": "X-ray",
    "summary": "முழங்கை எலும்பு முறிவு சரிசெய்யப்பட்டுள்ளது.",
}

MEDICINE_VISION = {
    "mode": "medicine", "drug_name": "Paracetamol", "manufacturer": "Cipla Ltd.",
    "mfg_date": "AUG.21", "expiry": "JUL.24",
    "summary": "Paracetamol Tablets IP ஒரு வலி நிவாரணி.",
}

MEDICINE_VISION_NO_MANUFACTURER = {
    "mode": "medicine", "drug_name": "Paracetamol", "manufacturer": "Not visible",
    "mfg_date": "AUG.21", "expiry": "JUL.24",
}


def test_lab_patient_name_lookup_returns_the_actual_name():
    r = _try_direct_lookup("what is the patient name of the lab report?", LAB_VISION, "lab")
    assert r is not None
    assert "JEGAJEEVAN RAM" in r["answer"]


def test_lab_lab_name_lookup_does_not_repeat_the_canned_summary():
    """This is the exact regression: before the fix, this question got the
    byte-identical blood-sugar summary three different times regardless of
    what was asked."""
    r = _try_direct_lookup("what is the laboratory name of this lab report?", LAB_VISION, "lab")
    assert r is not None
    assert "VDC" in r["answer"]
    assert r["answer"] != LAB_VISION["summary"]


def test_lab_doctor_name_not_extracted_is_answered_honestly():
    r = _try_direct_lookup("what is the doctor name on this report?", LAB_VISION, "lab")
    assert r is not None
    assert "VDC" not in r["answer"]  # must not substitute a different field
    assert "wasn't extracted" in r["answer"] or "ஆகவில்லை" in r["answer"]


def test_scan_provider_lookup():
    r = _try_direct_lookup("what is the scan center name of this scan?", SCAN_VISION, "scan")
    assert r is not None
    assert "METROPOLIS" in r["answer"]


def test_medicine_manufacturer_lookup_when_present():
    r = _try_direct_lookup("who is the manufacturer of this tablet?", MEDICINE_VISION, "medicine")
    assert r is not None
    assert "Cipla" in r["answer"]


def test_medicine_manufacturer_not_extracted_is_honest_not_hallucinated():
    """If the field genuinely wasn't read off the packaging, say so —
    never invent a manufacturer name."""
    r = _try_direct_lookup("who is the manufacturer of this tablet?", MEDICINE_VISION_NO_MANUFACTURER, "medicine")
    assert r is not None
    assert "wasn't extracted" in r["answer"] or "ஆகவில்லை" in r["answer"]


def test_batch_number_was_never_extracted_so_lookup_says_so_instead_of_guessing():
    """batch_number isn't even part of the vision extraction schema —
    confirms the lookup is honest about gaps instead of hallucinating."""
    r = _try_direct_lookup("what is the batch number of this paracetamol tablet?", MEDICINE_VISION, "medicine")
    assert r is not None
    assert "wasn't extracted" in r["answer"] or "ஆகவில்லை" in r["answer"]


def test_non_lookup_question_falls_through_to_normal_llm_path():
    """A broad question ('explain this report to me') must NOT be
    hijacked by the lookup fast-path — it needs the full LLM explanation."""
    r = _try_direct_lookup("can you explain this report to me in detail?", LAB_VISION, "lab")
    assert r is None


def test_assemble_result_shape_matches_for_lookup_and_llm_paths():
    """The lookup fast-path and the normal LLM path must produce the same
    structured_response shape, so the frontend UI cards render identically
    either way."""
    direct = _try_direct_lookup("what is the laboratory name of this lab report?", LAB_VISION, "lab")
    result = _assemble_result(direct, "lab", LAB_VISION, "en", "direct_lookup", 0.0)
    assert "structured_response" in result
    assert result["structured_response"]["lab_name"] == "VDC"
    assert result["model"] == "direct_lookup"
