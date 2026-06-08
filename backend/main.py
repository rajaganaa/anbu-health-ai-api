"""
Anbu Health AI — main.py
FastAPI backend for Tamil Nadu village patients.

Antahkarana Pipeline (Patent: 202641043947):
  Manas    → Question routing + classification
  Chitta   → Qdrant vector retrieval (medical PDFs)
  Buddhi   → Groq llama-3.3-70b reasoner (Tanglish)
  Ahamkara → Confidence scoring
  Sakshi   → Hallucination detection + verified answer
  Vision   → GPT-4o medicine/scan/lab image analysis

Author: Rajaganapathy M — M.Tech AI, SRM University
Patent: 202641043947
"""

import os
import uuid
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from dotenv import load_dotenv

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
    version="2.0.0",
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
        return None
    try:
        import wandb
        _wandb_run = wandb.init(
            project=os.environ.get("WANDB_PROJECT", "anbu-health-ai"),
            entity=os.environ.get("WANDB_ENTITY", "rajaganaa-ai"),
            name="anbu-health-ai-production",
            resume="allow",
            config={
                "product":  "Anbu Health AI",
                "author":   "Rajaganapathy M, SRM University",
                "patent":   "202641043947",
                "model":    "llama-3.3-70b-versatile (Groq)",
                "pipeline": "Antahkarana 7-step",
                "version":  "2.0.0",
            },
            tags=["production", "healthcare", "tamil", "qdrant", "groq"],
        )
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

# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("[STARTUP] Anbu Health AI API starting...")
    try:
        from rag.anbu_rag import build_index_if_needed
        build_index_if_needed()
        logger.info("[STARTUP] Qdrant index ready")
    except Exception as e:
        logger.warning(f"[STARTUP] RAG index skipped: {e}")
    _get_wandb()

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":   "healthy",
        "product":  "Anbu Health AI",
        "author":   "Rajaganapathy M, SRM University",
        "patent":   "202641043947",
        "version":  "2.0.0",
        "wandb":    "enabled" if os.environ.get("WANDB_API_KEY") else "disabled",
        "pipeline": "Antahkarana — Manas→Chitta→Buddhi→Ahamkara→Sakshi",
        "llm":      os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "vectordb": "Qdrant Cloud",
    }

@app.get("/")
async def root():
    return {"message": "Anbu Health AI API — /docs for Swagger, /health for status"}

# ── Main analysis endpoint ─────────────────────────────────────────────────────
@app.post("/api/analyze")
async def analyze(
    question: str = Form(...),
    mode: str = Form(default="general"),  # lab | scan | medicine | general
    image: Optional[UploadFile] = File(None),
):
    """
    Main endpoint for all analysis modes.
    mode: lab | scan | medicine | general
    """
    req_id = str(uuid.uuid4())[:8]
    t_start = time.time()
    logger.info(f"[{req_id}] mode={mode} q={question[:60]}")

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

            # Enrich question with vision context
            if vision_result.get("drug_name") and mode == "medicine":
                question = f"[Medicine: {vision_result['drug_name']}] {question}"
            elif vision_result.get("summary") and mode in ("lab", "scan"):
                question = f"[Report: {vision_result['summary'][:100]}] {question}"
        except Exception as e:
            logger.warning(f"[{req_id}] Vision failed: {e}")
            vision_result = {"error": str(e)}

    # ── Manas ─────────────────────────────────────────────────────────────────
    manas_r = p["manas"].route(question, mode)

    # ── Chitta ────────────────────────────────────────────────────────────────
    chitta_r = p["chitta"].retrieve(question, manas_r.get("entities", []), k=5)

    # ── Buddhi ────────────────────────────────────────────────────────────────
    buddhi_r = p["buddhi"].reason(
        question=question,
        context_str=chitta_r["context_str"],
        q_type=manas_r["question_type"],
        mode=mode,
        vision_info=vision_result,
    )

    # ── Ahamkara ──────────────────────────────────────────────────────────────
    ahamkara_r = p["ahamkara"].score(buddhi_r, chitta_r, question)

    # ── Sakshi ────────────────────────────────────────────────────────────────
    sakshi_r = p["sakshi"].verify(
        question=question,
        draft_answer=buddhi_r["draft_answer"],
        context_str=chitta_r["context_str"],
        sources=chitta_r["sources"],
        buddhi_result=buddhi_r,
        ahamkara_result=ahamkara_r,
    )

    latency = round(time.time() - t_start, 3)
    _log_wandb(req_id, question, mode, buddhi_r, ahamkara_r, sakshi_r, latency)

    # Cleanup
    if image_path and image_path.exists():
        try:
            image_path.unlink()
        except Exception:
            pass

    return JSONResponse(content={
        "request_id":      req_id,
        "mode":            mode,
        "question":        question,
        "total_latency_s": latency,
        "vision":          vision_result,
        "manas":           manas_r,
        "chitta": {
            "num_chunks":       chitta_r["num_chunks"],
            "sources":          chitta_r["sources"],
            "retrieval_method": chitta_r["retrieval_method"],
        },
        "buddhi": {
            "draft_answer":        buddhi_r["draft_answer"],
            "structured_response": buddhi_r["structured_response"],
            "detected_language":   buddhi_r["detected_language"],
            "pass2_fired":         buddhi_r["pass2_fired"],
            "model":               buddhi_r["model"],
            "latency_s":           buddhi_r["latency_s"],
        },
        "ahamkara":     ahamkara_r,
        "sakshi": {
            "verified":           sakshi_r["verified"],
            "final_answer":       sakshi_r["final_answer"],
            "hallucination_flags": sakshi_r["hallucination_flags"],
            "medical_disclaimer": sakshi_r["medical_disclaimer"],
        },
        "final_answer": sakshi_r["final_answer"],
        "sources":      chitta_r["sources"],
        "confidence":   ahamkara_r.get("confidence_score", 0),
    })

# ── Legacy endpoint (backward compat) ─────────────────────────────────────────
@app.post("/api/reason")
async def reason(
    question: str = Form(...),
    image: Optional[UploadFile] = File(None),
):
    return await analyze(question=question, mode="medicine" if image else "general", image=image)

# ── Utility endpoints ──────────────────────────────────────────────────────────
@app.post("/api/drug-interaction")
async def drug_interaction(drug1: str = Form(...), drug2: str = Form(...)):
    """Check if two drugs interact safely."""
    from engine.buddhi import Buddhi
    b = Buddhi()
    question = f"Drug interaction check: Is it safe to take {drug1} and {drug2} together? List any dangerous interactions."
    result = b.reason(question=question, context_str="", q_type="interaction", mode="general")
    return {"drug1": drug1, "drug2": drug2, "answer": result["draft_answer"], "structured": result["structured_response"]}

@app.post("/api/dosage")
async def dosage_calc(drug: str = Form(...), weight_kg: float = Form(...), age_group: str = Form(default="adult")):
    """Calculate dosage by weight."""
    from tools.dosage_calc import calculate_dosage
    result = calculate_dosage(drug, weight_kg, age_group)
    return {"drug": drug, "weight_kg": weight_kg, "age_group": age_group, "dosage": result}

@app.get("/api/sources")
async def list_sources():
    data_dir = Path(os.environ.get("ANBU_DATA_DIR", "./data/drug_guides"))
    pdfs = list(data_dir.glob("*.pdf")) if data_dir.exists() else []
    return {"sources": [p.name for p in pdfs], "count": len(pdfs)}

# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, log_level="info")
