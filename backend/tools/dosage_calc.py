"""tools/dosage_calc.py — Weight-based dosage calculator"""

DOSAGE_DB = {
    "paracetamol":   {"adult": "500-1000mg every 4-6 hrs (max 4g/day)", "child": "15mg/kg every 4-6 hrs (max 60mg/kg/day)", "unit": "mg/kg"},
    "ibuprofen":     {"adult": "200-400mg every 4-6 hrs (max 1.2g/day)", "child": "5-10mg/kg every 6-8 hrs", "unit": "mg/kg"},
    "amoxicillin":   {"adult": "250-500mg every 8 hrs", "child": "25mg/kg/day in 3 divided doses", "unit": "mg/kg/day"},
    "cetirizine":    {"adult": "10mg once daily", "child": "5mg once daily (6-12 yrs)", "unit": "fixed"},
    "metformin":     {"adult": "500-1000mg twice daily with food", "child": "Not recommended <10 yrs", "unit": "fixed"},
    "azithromycin":  {"adult": "500mg day 1, then 250mg days 2-5", "child": "10mg/kg on day 1, 5mg/kg days 2-5", "unit": "mg/kg"},
    "omeprazole":    {"adult": "20-40mg once daily before food", "child": "0.7-3.3mg/kg/day (max 20mg)", "unit": "mg/kg"},
    "pantoprazole":  {"adult": "40mg once daily", "child": "Not recommended <5 yrs", "unit": "fixed"},
}

def calculate_dosage(drug: str, weight_kg: float, age_group: str = "adult") -> dict:
    drug_key = drug.lower().strip()
    db_entry = DOSAGE_DB.get(drug_key)

    if not db_entry:
        return {
            "drug": drug,
            "weight_kg": weight_kg,
            "age_group": age_group,
            "dosage": "Not in database",
            "note": "Consult your doctor for this medicine",
        }

    base = db_entry.get(age_group, db_entry.get("adult", "Consult doctor"))
    unit = db_entry.get("unit", "fixed")

    calc_dose = None
    if unit == "mg/kg" and age_group == "child":
        # Example: paracetamol 15mg/kg
        import re
        m = re.search(r'(\d+)mg/kg', base)
        if m:
            calc_dose = f"{int(m.group(1)) * weight_kg:.0f}mg per dose"

    return {
        "drug":       drug,
        "weight_kg":  weight_kg,
        "age_group":  age_group,
        "dosage":     base,
        "calculated": calc_dose,
        "note":       "Always confirm with your doctor",
    }
