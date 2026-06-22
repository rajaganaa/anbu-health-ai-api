"""
Anbu Health AI — main.py  (COMPLIANCE PATCHED)
===============================================
Changes vs original:
  1. Imports compliance/safety_layer.py
  2. apply_compliance() called after Sakshi on every /api/analyze
  3. redact_sensitive_data() called before Supabase history save
  4. /api/dosage blocks Schedule H1 drugs
  5. New endpoints: /api/consent, /api/data/delete, /api/grievance

Patent: 202641043947 | Author: Rajaganapathy M, SRM University
"""

import os
import uuid
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import uvicorn
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from auth.otp import send_otp, verify_otp, resend_otp
from auth import otp as otp_module
from db import supabase_client as db

# ── NEW: Compliance imports ────────────────────────────────────────────────────
from compliance.safety_layer import (
    apply_compliance,
    redact_sensitive_data,
    is_schedule_h1_drug,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Anbu Health AI API",
    description=(
        "AI-powered medical assistant for Tamil Nadu village patients. "
        "Antahkarana reasoning pipeline — Patent: 202641043947. "
        "Author: Rajaganapathy M, SRM University."
    ),
    version="2.1.0",  # bumped for compliance release
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting ──────────────────────────────────────────────────────────────
_redis_url_for_limiter = os.environ.get("REDIS_URL", "").strip()
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_redis_url_for_limiter or "memory://",
    default_limits=[],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Prometheus metrics ─────────────────────────────────────────────────────────
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import Counter, Histogram, Gauge

    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    PIPELINE_LATENCY = Histogram(
        "anbu_pipeline_latency_seconds", "Total Antahkarana pipeline latency",
        ["mode"], buckets=(.5, 1, 2, 3, 5, 8, 13, 21, 34)
    )
    REQUESTS_BY_MODE = Counter(
        "anbu_requests_total", "Requests handled, by mode", ["mode"]
    )
    BUDDHI_FALLBACK = Counter(
        "anbu_buddhi_model_used_total", "Which Groq model answered", ["model"]
    )
    CONFIDENCE_SCORE = Gauge(
        "anbu_last_confidence_score", "Confidence score (0-100) of last response", ["mode"]
    )
    HALLUCINATION_FLAGS = Counter(
        "anbu_hallucination_flags_total", "Sakshi hallucination flags raised"
    )
    # NEW: compliance metrics
    COMPLIANCE_VIOLATIONS = Counter(
        "anbu_compliance_violations_total", "Compliance violations caught", ["violation_type"]
    )
    EMERGENCY_TRIGGERS = Counter(
        "anbu_emergency_triggers_total", "Emergency keywords detected"
    )
    _METRICS_ENABLED = True
    logger.info("[METRICS] Prometheus /metrics endpoint enabled")
except Exception as e:
    _METRICS_ENABLED = False
    logger.warning(f"[METRICS] prometheus_fastapi_instrumentator not available: {e}")


UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/tmp/anbu_uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Lazy pipeline components ───────────────────────────────────────────────────
_pipeline = None

def get_pipeline():
    global _pipeline
    if _pipeline is None:
        logger.info("[INIT] Loading Antahkarana pipeline...")
        from engine.manas import Manas
        from engine.chitta import Chitta
        from engine.buddhi import Buddhi
        from engine.ahamkara import Ahamkara
        from engine.sakshi import Sakshi
        _pipeline = {
            "manas":    Manas(),
            "chitta":   Chitta(),
            "buddhi":   Buddhi(),
            "ahamkara": Ahamkara(),
            "sakshi":   Sakshi(),
        }
        logger.info("[INIT] Pipeline ready")
    return _pipeline

# ── W&B ───────────────────────────────────────────────────────────────────────
_wandb_run = None

def _get_wandb():
    global _wandb_run
    if _wandb_run is not None:
        return _wandb_run
    api_key = os.environ.get("WANDB_API_KEY", "")
    if not api_key:
        logger.info("[W&B] WANDB_API_KEY not set — monitoring disabled")
        return None
    try:
        import wandb
        os.environ.setdefault("WANDB_MODE", "online")
        wandb.login(key=api_key, relogin=False, verify=False)
        _wandb_run = wandb.init(
            project=os.environ.get("WANDB_PROJECT") or "anbu-health-ai",
            entity=os.environ.get("WANDB_ENTITY") or "rajaganaa-ai",
            name="anbu-health-ai-production",
            resume="allow",
            settings=wandb.Settings(start_method="thread", init_timeout=20),
            config={
                "product":  "Anbu Health AI",
                "author":   "Rajaganapathy M, SRM University",
                "patent":   "202641043947",
                "model":    "llama-3.3-70b-versatile (Groq)",
                "pipeline": "Antahkarana 7-step",
                "version":  "2.1.0",
            },
            tags=["production", "healthcare", "tamil", "qdrant", "groq", "compliance"],
        )
        logger.info(f"[W&B] Initialized — run: {_wandb_run.url if _wandb_run else 'n/a'}")
        return _wandb_run
    except Exception as e:
        logger.warning(f"[W&B] Init failed: {e}")
        return None

def _log_wandb(req_id, question, mode, buddhi_r, ahamkara_r, sakshi_r, latency):
    run = _get_wandb()
    if not run:
        return
    try:
        conf = ahamkara_r.get("confidence_score", 0)
        conf_f = conf / 100.0 if conf > 1 else conf
        run.log({
            "latency/total_s":             latency,
            "latency/buddhi_s":            buddhi_r.get("latency_s", 0),
            "quality/confidence":          conf_f,
            "quality/sakshi_verified":     int(sakshi_r.get("verified", True)),
            "quality/hallucination_count": len(sakshi_r.get("hallucination_flags", [])),
            "usage/mode":                  mode,
            "usage/is_tanglish":           int(buddhi_r.get("detected_language") == "ta"),
            "pipeline/pass2_fired":        int(buddhi_r.get("pass2_fired", False)),
        })
    except Exception as e:
        logger.debug(f"[W&B] Log skipped: {e}")

def _log_metrics(mode, buddhi_r, ahamkara_r, sakshi_r, latency, compliance_r=None):
    if not _METRICS_ENABLED:
        return
    try:
        PIPELINE_LATENCY.labels(mode=mode).observe(latency)
        REQUESTS_BY_MODE.labels(mode=mode).inc()
        BUDDHI_FALLBACK.labels(model=buddhi_r.get("model", "unknown")).inc()
        conf = ahamkara_r.get("confidence_score", 0)
        CONFIDENCE_SCORE.labels(mode=mode).set(conf)
        HALLUCINATION_FLAGS.inc(len(sakshi_r.get("hallucination_flags", [])))
        # NEW: compliance metrics
        if compliance_r:
            for vtype in compliance_r.get("violations", []):
                COMPLIANCE_VIOLATIONS.labels(violation_type=vtype).inc()
            if compliance_r.get("emergency_alert"):
                EMERGENCY_TRIGGERS.inc()
    except Exception as e:
        logger.debug(f"[METRICS] Log skipped: {e}")

# ── Startup ────────────────────────────────────────────────────────────────────
_startup_complete = False

@app.on_event("startup")
async def startup_event():
    global _startup_complete
    logger.info("[STARTUP] Anbu Health AI API v2.1.0 (compliance) starting...")
    try:
        from rag.anbu_rag import build_index_if_needed
        build_index_if_needed()
        logger.info("[STARTUP] Qdrant index ready")
    except Exception as e:
        logger.warning(f"[STARTUP] RAG index skipped: {e}")
    _get_wandb()
    try:
        get_pipeline()
        logger.info("[STARTUP] Pipeline warmed")
    except Exception as e:
        logger.error(f"[STARTUP] Pipeline warm-up failed: {e} — will retry lazily on first request")
    _startup_complete = True

# ── Health & readiness ─────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":     "healthy",
        "product":    "Anbu Health AI",
        "author":     "Rajaganapathy M, SRM University",
        "patent":     "202641043947",
        "version":    "2.1.0",
        "compliance": "Telemedicine Guidelines 2020 + DPDP Act 2023",
        "wandb":      "enabled" if os.environ.get("WANDB_API_KEY") else "disabled",
        "pipeline":   "Antahkarana — Manas→Chitta→Buddhi→Ahamkara→Sakshi→Compliance",
        "llm":        os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "vectordb":   "Qdrant Cloud",
        "supabase":   "enabled" if db.is_enabled() else "disabled (using localStorage fallback)",
        "otp":        "dev_mode" if otp_module.DEV_MODE else "msg91",
        "metrics":    "enabled" if _METRICS_ENABLED else "disabled",
    }

@app.get("/ready")
async def ready():
    if not _startup_complete:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "pipeline still warming up"})
    return {"ready": True}

@app.get("/")
async def root():
    return {"message": "Anbu Health AI API — /docs for Swagger, /health for status"}

# ── Phone OTP Authentication (MSG91) ──────────────────────────────────────────
@app.post("/api/auth/send-otp")
@app.post("/api/send-otp")
@limiter.limit("5/minute")
async def auth_send_otp(request: Request, phone: str = Form(...)):
    result = send_otp(phone)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to send OTP"))
    return result

@app.post("/api/auth/resend-otp")
async def auth_resend_otp(phone: str = Form(...)):
    result = resend_otp(phone)
    if not result.get("success"):
        raise HTTPException(status_code=429, detail=result.get("error", "Failed to resend OTP"))
    return result

@app.post("/api/auth/verify-otp")
@app.post("/api/verify-otp")
@limiter.limit("10/minute")
async def auth_verify_otp(request: Request, phone: str = Form(...), otp: str = Form(...)):
    result = verify_otp(phone, otp)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Verification failed"))
    user = db.get_or_create_user(phone)
    status_r = db.get_prompt_status(phone)
    return {
        "success":  True,
        "phone":    phone,
        "user_id":  user.get("id"),
        "prompts":  status_r,
    }

# ── Firebase Phone Auth session ────────────────────────────────────────────────
@app.post("/api/auth/firebase-session")
async def firebase_session(request: Request):
    from auth.firebase_auth import verify_id_token, bearer_token, enabled
    token = bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if not enabled():
        raise HTTPException(status_code=503, detail="Firebase Admin SDK not configured")
    try:
        info = verify_id_token(token)
    except Exception as e:
        logger.warning("[FIREBASE] Token verification failed: %s", e)
        raise HTTPException(status_code=401, detail=f"Invalid Firebase token: {e}")
    phone = info["phone"].lstrip("+")
    user = db.get_or_create_user(phone)
    status_r = db.get_prompt_status(phone)
    return {
        "success":  True,
        "phone":    phone,
        "user_id":  user.get("id"),
        "prompts":  status_r,
    }

# ── User status / history ──────────────────────────────────────────────────────
@app.get("/api/user/status")
async def user_status(phone: str):
    return db.get_prompt_status(phone)

@app.get("/api/user/history")
async def user_history(phone: str, limit: int = 50):
    return {"phone": phone, "messages": db.get_history(phone, limit=limit)}

# ── COMPLIANCE: Consent recording (DPDP Act 2023, Section 6) ──────────────────
@app.post("/api/consent")
async def record_consent(
    phone: str = Form(...),
    consent_given: bool = Form(...),
    consent_version: str = Form(default="1.0"),
):
    """
    DPDP Act 2023 §6: Consent must be free, specific, informed, unconditional.
    Called by frontend on first login after user taps "I Agree".
    """
    try:
        db.record_consent(phone, consent_given, consent_version)
        logger.info(f"[CONSENT] phone={phone[-4:]}**** given={consent_given} v={consent_version}")
    except Exception as e:
        logger.warning(f"[CONSENT] Save failed: {e}")
    return {"success": True, "consent_recorded": consent_given}

# ── COMPLIANCE: Data deletion (DPDP Act 2023, Section 12 — Right to Erasure) ──
@app.delete("/api/data/delete")
@limiter.limit("3/minute")
async def delete_user_data(
    request: Request,
    phone: str = Form(...),
    otp: str = Form(...),
):
    """
    DPDP Act 2023 §12: Data Principal has the right to erasure.
    Requires OTP verification before deleting. Deletes: users, chat_history,
    prompt_usage, otp_codes, user_consent rows for this phone.
    """
    verify_result = verify_otp(phone, otp)
    if not verify_result.get("success"):
        raise HTTPException(status_code=401, detail="OTP verification failed — cannot delete data")
    try:
        db.delete_all_user_data(phone)
        logger.info(f"[GDPR/DPDP] All data deleted for phone={phone[-4:]}****")
    except Exception as e:
        logger.error(f"[GDPR/DPDP] Delete failed: {e}")
        raise HTTPException(status_code=500, detail="Data deletion failed — contact grievance officer")
    return {
        "success": True,
        "message": "உங்கள் எல்லா தகவல்களும் நிரந்தரமாக delete ஆகிவிட்டன. / All your data has been permanently deleted.",
    }

# ── COMPLIANCE: Grievance officer endpoint (IT Rules 2021, Rule 4) ─────────────
@app.post("/api/grievance")
@limiter.limit("5/minute")
async def submit_grievance(
    request: Request,
    phone: str = Form(default=None),
    complaint: str = Form(...),
    category: str = Form(default="general"),  # harmful_advice | data_privacy | wrong_info | other
):
    """
    IT Rules 2021 Rule 4(2): Intermediary must have a grievance redressal mechanism.
    Acknowledgement within 24 hours, resolution within 15 working days.
    """
    if len(complaint) < 10:
        raise HTTPException(status_code=400, detail="Please describe your complaint in more detail.")
    grievance_id = f"AHA-{str(uuid.uuid4())[:8].upper()}"
    try:
        db.save_grievance(grievance_id, phone, category, complaint)
        logger.info(f"[GRIEVANCE] id={grievance_id} category={category}")
    except Exception as e:
        logger.warning(f"[GRIEVANCE] Save failed: {e} — still returning success")
    return {
        "grievance_id":        grievance_id,
        "acknowledged":        True,
        "resolution_timeline": "15 working days",
        "contact":             os.environ.get("GRIEVANCE_EMAIL", "grievance@anbuhealth.ai"),
        "message": (
            f"Your complaint (ID: {grievance_id}) has been received. "
            "We will respond within 15 working days as required by IT Rules 2021."
        ),
    }

# ── Main analysis endpoint ─────────────────────────────────────────────────────
@app.post("/api/analyze")
@limiter.limit("100/minute")
async def analyze(
    request: Request,
    question: str = Form(...),
    mode: str = Form(default="general"),
    image: Optional[UploadFile] = File(None),
    phone: Optional[str] = Form(default=None),
    chat_id: Optional[str] = Form(default=None),
):
    req_id = str(uuid.uuid4())[:8]
    t_start = time.time()
    logger.info(f"[{req_id}] mode={mode} phone={phone} q={question[:60]}")

    # ── Server-side daily prompt limit ────────────────────────────────────────
    if phone:
        status_r = db.get_prompt_status(phone)
        if not status_r["allowed"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error":   "daily_limit_reached",
                    "message": "Today's 20 prompts முடிந்தது. நாளைக்கு வா! (Resets at midnight)",
                    "prompts": status_r,
                },
            )

    p = get_pipeline()

    # ── Vision ────────────────────────────────────────────────────────────────
    vision_result = None
    image_path = None

    if image and image.filename:
        try:
            image_path = UPLOAD_DIR / f"{req_id}_{image.filename}"
            contents = await image.read()
            with open(image_path, "wb") as f:
                f.write(contents)

            from vision.anbu_vision import analyze_image
            vision_result = analyze_image(str(image_path), mode)
            logger.info(f"[{req_id}] Vision: {vision_result.get('summary', 'done')[:50]}")

            if vision_result.get("drug_name") and mode == "medicine":
                question = f"[Medicine: {vision_result['drug_name']}] {question}"
            elif vision_result.get("summary") and mode in ("lab", "scan"):
                question = f"[Report: {vision_result['summary'][:100]}] {question}"

            if mode == "medicine" and vision_result and not vision_result.get("error"):
                try:
                    from tools.medical_tools import run_tools_for_medicine
                    tool_results = run_tools_for_medicine(vision_result, question)
                    vision_result["tool_results"] = tool_results
                    logger.info(f"[{req_id}] Tools: {list(tool_results.keys())}")
                except Exception as te:
                    logger.warning(f"[{req_id}] Tools failed: {te}")

        except Exception as e:
            logger.warning(f"[{req_id}] Vision failed: {e}")
            vision_result = {"error": str(e)}

    # ── Manas → Chitta → Buddhi → Ahamkara → Sakshi ──────────────────────────
    manas_r    = p["manas"].route(question, mode)
    chitta_r   = p["chitta"].retrieve(question, manas_r.get("entities", []), k=5)
    buddhi_r   = p["buddhi"].reason(
        question=question,
        context_str=chitta_r["context_str"],
        q_type=manas_r["question_type"],
        mode=mode,
        vision_info=vision_result,
    )
    ahamkara_r = p["ahamkara"].score(buddhi_r, chitta_r, question)
    sakshi_r   = p["sakshi"].verify(
        question=question,
        draft_answer=buddhi_r["draft_answer"],
        context_str=chitta_r["context_str"],
        sources=chitta_r["sources"],
        buddhi_result=buddhi_r,
        ahamkara_result=ahamkara_r,
    )

    # ── COMPLIANCE GATE (NEW) ─────────────────────────────────────────────────
    compliance_r = apply_compliance(
        question=question,
        final_answer=sakshi_r["final_answer"],
        lang=buddhi_r.get("detected_language", "en"),
        mode=mode,
    )
    compliant_answer = compliance_r["final_answer"]

    # Audit trail (Telemedicine Guidelines 2020 + DPDP Act) — best-effort,
    # never blocks the response if Supabase is slow/unavailable.
    try:
        db.log_compliance(
            request_id=req_id,
            phone=phone,
            mode=mode,
            violations_found=compliance_r["violations_found"],
            violation_types=compliance_r["violations"],
            emergency_triggered=bool(compliance_r.get("emergency_alert")),
        )
    except Exception as e:
        logger.debug(f"[{req_id}] Compliance audit log skipped: {e}")

    latency = round(time.time() - t_start, 3)
    _log_wandb(req_id, question, mode, buddhi_r, ahamkara_r, sakshi_r, latency)
    _log_metrics(mode, buddhi_r, ahamkara_r, sakshi_r, latency, compliance_r)

    # ── Supabase: prompt count + redacted chat history ─────────────────────────
    prompts_status = None
    if phone:
        prompts_status = db.increment_prompt_count(phone)
        try:
            # DPDP Act: redact sensitive data before storing
            safe_question = redact_sensitive_data(question)
            safe_answer   = redact_sensitive_data(compliant_answer)
            db.save_message(phone, "user",      safe_question, mode=mode, chat_id=chat_id)
            db.save_message(phone, "assistant", safe_answer,   mode=mode,
                            structured=buddhi_r.get("structured_response"), chat_id=chat_id)
        except Exception as e:
            logger.warning(f"[{req_id}] History save failed: {e}")

    # Cleanup uploaded image
    if image_path and image_path.exists():
        try:
            image_path.unlink()
        except Exception:
            pass

    return JSONResponse(content={
        "request_id":            req_id,
        "mode":                  mode,
        "question":              question,
        "total_latency_s":       latency,
        "vision":                vision_result,
        "manas":                 manas_r,
        "chitta": {
            "num_chunks":        chitta_r["num_chunks"],
            "sources":           chitta_r["sources"],
            "retrieval_method":  chitta_r["retrieval_method"],
        },
        "buddhi": {
            "draft_answer":        buddhi_r["draft_answer"],
            "structured_response": buddhi_r["structured_response"],
            "detected_language":   buddhi_r["detected_language"],
            "pass2_fired":         buddhi_r["pass2_fired"],
            "model":               buddhi_r["model"],
            "latency_s":           buddhi_r["latency_s"],
        },
        "ahamkara":    ahamkara_r,
        "sakshi": {
            "verified":            sakshi_r["verified"],
            "final_answer":        sakshi_r["final_answer"],
            "hallucination_flags": sakshi_r["hallucination_flags"],
            "medical_disclaimer":  sakshi_r["medical_disclaimer"],
        },
        # ── Compliance fields (NEW) ────────────────────────────────────────────
        "final_answer":          compliant_answer,
        "compliance_disclaimer": compliance_r["compliance_disclaimer"],
        "emergency_alert":       compliance_r.get("emergency_alert"),   # None or string
        "sources":               chitta_r["sources"],
        "confidence":            ahamkara_r.get("confidence_score", 0),
        "prompts":               prompts_status,
    })

# ── Legacy endpoint ────────────────────────────────────────────────────────────
@app.post("/api/reason")
@limiter.limit("15/minute")
async def reason(
    request: Request,
    question: str = Form(...),
    image: Optional[UploadFile] = File(None),
):
    return await analyze(
        request=request,
        question=question,
        mode="medicine" if image else "general",
        image=image,
        phone=None,
        chat_id=None,
    )

# ── Drug interaction ───────────────────────────────────────────────────────────
@app.post("/api/drug-interaction")
async def drug_interaction(drug1: str = Form(...), drug2: str = Form(...)):
    from engine.buddhi import Buddhi
    b = Buddhi()
    question = f"Drug interaction check: Is it safe to take {drug1} and {drug2} together? List any dangerous interactions."
    result = b.reason(question=question, context_str="", q_type="interaction", mode="general")
    return {
        "drug1":       drug1,
        "drug2":       drug2,
        "answer":      result["draft_answer"],
        "structured":  result["structured_response"],
        "disclaimer":  "This is for reference only. Always confirm with a Registered Medical Practitioner.",
    }

# ── Dosage calculator — COMPLIANCE: Schedule H1 blocked ───────────────────────
@app.post("/api/dosage")
async def dosage_calc(
    drug: str = Form(...),
    weight_kg: float = Form(...),
    age_group: str = Form(default="adult"),
):
    """
    COMPLIANCE NOTE (Drug & Cosmetics Act):
    Schedule H/H1 drugs may only be dispensed on prescription.
    This endpoint blocks those drugs entirely.
    """
    # Block Schedule H/H1 drugs
    if is_schedule_h1_drug(drug):
        return JSONResponse(status_code=403, content={
            "error": "prescription_only",
            "message": (
                f"'{drug}' is a Schedule H/H1 prescription drug under India's Drug & Cosmetics Act. "
                "Dosage must be determined by a Registered Medical Practitioner only."
            ),
            "message_ta": (
                f"'{drug}' Schedule H/H1 prescription மருந்து. "
                "இதன் dosage Registered Doctor மட்டும் தர முடியும்."
            ),
        })

    from tools.dosage_calc import calculate_dosage
    result = calculate_dosage(drug, weight_kg, age_group)
    return {
        "drug":        drug,
        "weight_kg":   weight_kg,
        "age_group":   age_group,
        "dosage":      result,
        "disclaimer":  "Reference only. Confirm dosage with a Registered Medical Practitioner before use.",
        "disclaimer_ta": "Reference மட்டும். எந்த மருந்தும் Doctor confirm பண்ணிட்டு எடுங்க.",
    }

# ── Sources ────────────────────────────────────────────────────────────────────
@app.get("/api/sources")
async def list_sources():
    data_dir = Path(os.environ.get("ANBU_DATA_DIR", "./data/drug_guides"))
    pdfs = list(data_dir.glob("*.pdf")) if data_dir.exists() else []
    return {"sources": [p.name for p in pdfs], "count": len(pdfs)}

# ── Text-to-Speech ─────────────────────────────────────────────────────────────
@app.post("/api/tts")
async def text_to_speech(request: Request):
    from tools.sarvam_tts import synthesize_speech
    body = await request.json()
    text = (body.get("text") or "").strip()
    lang = body.get("lang") or "ta-IN"
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    audio_bytes, audio_fmt = synthesize_speech(text, language_code=lang)
    if not audio_bytes:
        raise HTTPException(status_code=502, detail="TTS service unavailable")
    return Response(content=audio_bytes, media_type=f"audio/{audio_fmt}")

# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
