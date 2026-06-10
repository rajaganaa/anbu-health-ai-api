"""
tools/medical_tools.py — Merged Medical Tools v1.0
Ported from Antahkarana MedAssist into Anbu Health AI.

Features:
1. check_expiry(date_str) — checks if medicine is expired
2. get_fda_adverse_events(drug_name) — real FDA FAERS data
3. drug_interaction_check(drugs) — checks known interactions
4. weight_based_dosage(drug, weight_kg, age_years) — calculates safe dose

No langchain dependency — pure Python + requests only.
"""

import re
import requests
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)

# ── MONTH MAP ─────────────────────────────────────────────────────────────────
MONTHS = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,
    'apr':4,'april':4,'may':5,'jun':6,'june':6,'jul':7,'july':7,
    'aug':8,'august':8,'sep':9,'sept':9,'september':9,
    'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12,
}

# ── KNOWN DRUG INTERACTIONS ───────────────────────────────────────────────────
# Source: standard clinical pharmacology references
INTERACTIONS = {
    frozenset(["warfarin","aspirin"]):           {"level":"HIGH","effect":"Increased bleeding risk — aspirin enhances warfarin anticoagulation"},
    frozenset(["warfarin","ibuprofen"]):         {"level":"HIGH","effect":"Serious bleeding risk — NSAIDs increase warfarin effect"},
    frozenset(["metformin","alcohol"]):          {"level":"HIGH","effect":"Lactic acidosis risk — avoid alcohol with metformin"},
    frozenset(["metformin","contrast dye"]):     {"level":"HIGH","effect":"Kidney damage risk — stop metformin before contrast procedures"},
    frozenset(["aspirin","ibuprofen"]):          {"level":"MEDIUM","effect":"Reduced aspirin effectiveness — take aspirin 2hrs before ibuprofen"},
    frozenset(["amlodipine","simvastatin"]):     {"level":"MEDIUM","effect":"Myopathy risk — simvastatin dose should not exceed 20mg with amlodipine"},
    frozenset(["paracetamol","alcohol"]):        {"level":"MEDIUM","effect":"Liver damage risk — avoid >2 drinks/day with paracetamol"},
    frozenset(["ciprofloxacin","antacids"]):     {"level":"MEDIUM","effect":"Reduced absorption — take ciprofloxacin 2hrs before antacids"},
    frozenset(["atorvastatin","clarithromycin"]):{"level":"MEDIUM","effect":"Statin toxicity risk — clarithromycin inhibits statin metabolism"},
    frozenset(["digoxin","amiodarone"]):         {"level":"HIGH","effect":"Digoxin toxicity risk — amiodarone increases digoxin levels"},
    frozenset(["ssri","tramadol"]):              {"level":"HIGH","effect":"Serotonin syndrome risk — potentially life-threatening"},
    frozenset(["lithium","ibuprofen"]):          {"level":"HIGH","effect":"Lithium toxicity — NSAIDs reduce lithium clearance"},
    frozenset(["methotrexate","aspirin"]):       {"level":"HIGH","effect":"Methotrexate toxicity — aspirin reduces its clearance"},
    frozenset(["insulin","alcohol"]):            {"level":"HIGH","effect":"Severe hypoglycemia risk — alcohol masks low blood sugar symptoms"},
    frozenset(["paracetamol","ibuprofen"]):      {"level":"LOW","effect":"Generally safe to combine — different mechanisms, often used together"},
}

# FDA drug name aliases (Indian brand → generic)
FDA_ALIASES = {
    "paracetamol":"acetaminophen","crocin":"acetaminophen",
    "dolo":"acetaminophen","calpol":"acetaminophen",
    "tylenol":"acetaminophen","combiflam":"ibuprofen",
    "brufen":"ibuprofen","advil":"ibuprofen",
    "pantoprazole":"pantoprazole","pantop":"pantoprazole",
    "pan":"pantoprazole","omeprazole":"omeprazole",
    "omez":"omeprazole","metformin":"metformin",
    "glucophage":"metformin","glycomet":"metformin",
    "atorvastatin":"atorvastatin","lipitor":"atorvastatin",
    "amlodipine":"amlodipine","norvasc":"amlodipine",
    "amoxicillin":"amoxicillin","mox":"amoxicillin",
    "azithromycin":"azithromycin","zithromax":"azithromycin",
    "ciprofloxacin":"ciprofloxacin","cifran":"ciprofloxacin",
}

# Dosage guidelines (mg/kg)
DOSAGE_DB = {
    "paracetamol":  {"min_kg":10,"max_kg":15,"max_single":1000,"max_daily":4000,"freq":4,"interval_h":4,"min_age":0.25,"note":"Avoid alcohol. Max 4 doses/day."},
    "acetaminophen":{"min_kg":10,"max_kg":15,"max_single":1000,"max_daily":4000,"freq":4,"interval_h":4,"min_age":0.25,"note":"Avoid alcohol. Max 4 doses/day."},
    "ibuprofen":    {"min_kg":5, "max_kg":10, "max_single":400, "max_daily":1200,"freq":3,"interval_h":6,"min_age":0.5, "note":"Take with food. Not for <6 months."},
    "amoxicillin":  {"min_kg":25,"max_kg":50, "max_single":500, "max_daily":3000,"freq":3,"interval_h":8,"min_age":0,   "note":"Complete full course. Check for penicillin allergy."},
    "metformin":    {"min_kg":None,"max_kg":None,"max_single":1000,"max_daily":2000,"freq":2,"interval_h":12,"min_age":10,"note":"Take with meals. Monitor kidney function."},
    "omeprazole":   {"min_kg":None,"max_kg":None,"max_single":40, "max_daily":80, "freq":1,"interval_h":24,"min_age":1,  "note":"Take 30 min before meals."},
    "pantoprazole": {"min_kg":None,"max_kg":None,"max_single":40, "max_daily":80, "freq":1,"interval_h":24,"min_age":5,  "note":"Take 30-60 min before meals."},
    "azithromycin": {"min_kg":10,"max_kg":10,"max_single":500,"max_daily":500,"freq":1,"interval_h":24,"min_age":0.5,"note":"Complete full course. Take on empty stomach."},
}


# ── 1. EXPIRY CHECKER ─────────────────────────────────────────────────────────
def parse_expiry(s: str) -> Optional[Tuple[int,int]]:
    s = s.strip().lower()
    for pattern, handler in [
        (r'([a-z]+)\s*(\d{4})',   lambda m: (int(m[1]), MONTHS.get(m[0]))),
        (r'(\d{4})\s*([a-z]+)',   lambda m: (int(m[0]), MONTHS.get(m[1]))),
        (r'(\d{1,2})[/\-](\d{4})',lambda m: (int(m[1]), int(m[0])) if 1<=int(m[0])<=12 else None),
        (r'(\d{4})[/\-](\d{1,2})',lambda m: (int(m[0]), int(m[1])) if 1<=int(m[1])<=12 else None),
        (r'(\d{1,2})[/\-](\d{2})$',lambda m: (2000+int(m[1]), int(m[0])) if 1<=int(m[0])<=12 else None),
        (r'^(\d{4})$',            lambda m: (int(m[0]), 12)),
    ]:
        match = re.match(pattern, s)
        if match:
            result = handler(match.groups())
            if result and result[1]: return result
    return None

def check_expiry(date_str: str) -> Dict:
    """Check if medicine is expired. Returns dict with status, days, message."""
    parsed = parse_expiry(date_str)
    if not parsed:
        return {"status":"unknown","message":f"Cannot parse date: '{date_str}'. Use format like 'Dec 2025' or '12/2025'"}

    year, month = parsed
    # Last day of expiry month
    if month == 12:
        expiry_date = date(year+1,1,1) - timedelta(days=1)
    else:
        expiry_date = date(year,month+1,1) - timedelta(days=1)

    today     = date.today()
    days_diff = (expiry_date - today).days
    month_names = ["","January","February","March","April","May","June","July","August","September","October","November","December"]

    if days_diff < 0:
        return {
            "status":    "EXPIRED",
            "days":      abs(days_diff),
            "expiry_str":f"{month_names[month]} {year}",
            "message":   f"⚠️ EXPIRED — {abs(days_diff)} days ago ({month_names[month]} {year}). Do NOT use this medicine.",
            "safe":      False,
        }
    elif days_diff <= 30:
        return {
            "status":    "EXPIRING_SOON",
            "days":      days_diff,
            "expiry_str":f"{month_names[month]} {year}",
            "message":   f"⚠️ Expiring in {days_diff} days ({month_names[month]} {year}). Use soon or replace.",
            "safe":      True,
        }
    else:
        months_left = days_diff // 30
        return {
            "status":    "VALID",
            "days":      days_diff,
            "expiry_str":f"{month_names[month]} {year}",
            "message":   f"✅ Valid — expires {month_names[month]} {year} ({months_left} months remaining).",
            "safe":      True,
        }


# ── 2. FDA ADVERSE EVENTS ─────────────────────────────────────────────────────
def get_fda_adverse_events(drug_name: str) -> Dict:
    """Get top adverse events from FDA FAERS database. No API key needed."""
    fda_name = FDA_ALIASES.get(drug_name.lower().strip(), drug_name.lower().strip())
    logger.info(f"[TOOLS] FDA query: {drug_name} → {fda_name}")

    try:
        resp = requests.get(
            "https://api.fda.gov/drug/event.json",
            params={
                "search": f'patient.drug.openfda.generic_name:"{fda_name}"',
                "count":  "patient.reaction.reactionmeddrapt.exact",
                "limit":  10,
            },
            timeout=8,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            reactions = [{"reaction": r["term"], "reports": r["count"]} for r in results[:8]]
            return {
                "drug":      drug_name,
                "fda_name":  fda_name,
                "reactions": reactions,
                "source":    "FDA FAERS",
                "found":     True,
            }
        else:
            return {"drug": drug_name, "found": False, "message": "Not found in FDA database"}
    except Exception as e:
        logger.warning(f"[TOOLS] FDA API error: {e}")
        return {"drug": drug_name, "found": False, "message": f"FDA API unavailable: {e}"}


# ── 3. DRUG INTERACTION CHECK ─────────────────────────────────────────────────
def check_drug_interactions(drug_list: List[str]) -> List[Dict]:
    """
    Check interactions between a list of drugs.
    Returns list of found interactions with severity level.
    """
    found = []
    normalized = [d.lower().strip() for d in drug_list]

    for i, drug1 in enumerate(normalized):
        for drug2 in normalized[i+1:]:
            key = frozenset([drug1, drug2])
            if key in INTERACTIONS:
                interaction = INTERACTIONS[key]
                found.append({
                    "drug1":  drug1,
                    "drug2":  drug2,
                    "level":  interaction["level"],
                    "effect": interaction["effect"],
                })

    # Also check aliases
    aliased = [FDA_ALIASES.get(d, d) for d in normalized]
    for i, drug1 in enumerate(aliased):
        for drug2 in aliased[i+1:]:
            key = frozenset([drug1, drug2])
            if key in INTERACTIONS:
                interaction = INTERACTIONS[key]
                entry = {
                    "drug1":  drug_list[i],
                    "drug2":  drug_list[aliased.index(drug2)] if drug2 in aliased else drug2,
                    "level":  interaction["level"],
                    "effect": interaction["effect"],
                }
                if entry not in found:
                    found.append(entry)

    return found


# ── 4. WEIGHT-BASED DOSAGE ────────────────────────────────────────────────────
def calculate_dosage(drug: str, weight_kg: float, age_years: float) -> Dict:
    """Calculate safe dosage based on weight and age."""
    drug_key = FDA_ALIASES.get(drug.lower(), drug.lower())
    guide    = DOSAGE_DB.get(drug_key)

    if not guide:
        return {"drug": drug, "found": False, "message": f"Dosage guide not available for {drug}"}

    if age_years < guide["min_age"]:
        return {
            "drug":    drug,
            "found":   True,
            "safe":    False,
            "message": f"Not recommended for age {age_years} years (minimum: {guide['min_age']} years)",
        }

    if guide["min_kg"]:
        min_dose = round(guide["min_kg"] * weight_kg, 1)
        max_dose = round(guide["max_kg"] * weight_kg, 1)
        # Cap at maximum single dose
        min_dose = min(min_dose, guide["max_single"])
        max_dose = min(max_dose, guide["max_single"])
    else:
        # Fixed dosing (not weight-based)
        min_dose = max_dose = guide["max_single"]

    return {
        "drug":          drug,
        "found":         True,
        "safe":          True,
        "weight_kg":     weight_kg,
        "single_dose":   f"{min_dose}-{max_dose}mg" if min_dose != max_dose else f"{min_dose}mg",
        "frequency":     f"Every {guide['interval_h']} hours ({guide['freq']}x/day)",
        "max_daily":     f"{guide['max_daily']}mg",
        "note":          guide["note"],
        "disclaimer":    "Doctor prescription required. This is reference only.",
    }


# ── UNIFIED TOOL RUNNER ───────────────────────────────────────────────────────
def run_tools_for_medicine(vision_info: Dict, question: str) -> Dict:
    """
    Auto-run relevant tools based on vision output and question.
    Called by main.py after vision analysis.
    """
    results = {}

    # Extract drug name from vision
    drug_name = (
        vision_info.get("generic_name") or
        vision_info.get("drug_name") or
        vision_info.get("brand_name") or ""
    ).strip()

    # 1. Expiry check — if expiry detected by vision
    expiry_str = vision_info.get("expiry", "")
    if expiry_str and expiry_str not in ("Not visible", "N/A", ""):
        results["expiry"] = check_expiry(expiry_str)
        logger.info(f"[TOOLS] Expiry check: {expiry_str} → {results['expiry']['status']}")

    # 2. FDA adverse events — if drug identified and question asks about side effects
    side_effect_keywords = ["side effect","adverse","reaction","safe","danger","risk","harm","பக்க"]
    if drug_name and any(k in question.lower() for k in side_effect_keywords):
        results["fda"] = get_fda_adverse_events(drug_name)
        logger.info(f"[TOOLS] FDA query for: {drug_name}")

    # 3. Interaction check — if multiple drugs mentioned in question
    words    = re.findall(r'\b[a-zA-Z]{4,}\b', question)
    drug_candidates = [w for w in words if w.lower() in DOSAGE_DB or w.lower() in FDA_ALIASES]
    if drug_name:
        drug_candidates.append(drug_name)
    drug_candidates = list(set(drug_candidates))

    interact_keywords = ["together","combine","mix","same time","both","interaction","சேர்த்து","இரண்டும்"]
    if len(drug_candidates) >= 2 and any(k in question.lower() for k in interact_keywords):
        results["interactions"] = check_drug_interactions(drug_candidates)
        logger.info(f"[TOOLS] Interaction check: {drug_candidates}")

    return results
