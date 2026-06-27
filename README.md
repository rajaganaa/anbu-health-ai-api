<div align="center">

# ⚙️ Anbu Health AI — Backend & Reasoning Engine

### FastAPI service powering a bilingual medical AI assistant

**5-stage reasoning pipeline · RAG · Vision · Voice · Compliance-aware**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Container%20Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com)
[![Azure](https://img.shields.io/badge/Azure-Container%20Apps-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com)
[![Status](https://img.shields.io/badge/Status-Production-success)]()

[🌐 Live App](https://anbuclinic.me) · [💻 Frontend Repo](https://github.com/rajaganaa/anbu-health-ai) · [📄 Patent Filed](https://github.com/rajaganaa/antahkarana-reasoning-framework)

</div>

---

## 🔗 Connected Repository

This is the **backend / AI engine**. The React UI consuming this API lives in a separate repo:

➡️ **Frontend:** [`anbu-health-ai`](https://github.com/rajaganaa/anbu-health-ai)

---

## 💡 What This Service Does

This API is the reasoning core behind **Anbu Health AI** — a bilingual (Tamil + English) medical assistant. It receives a question and/or an uploaded document (lab report, scan, medicine photo), routes it through a multi-stage reasoning pipeline, and returns a verified, safety-checked, plain-language answer.

It's built around one principle that matters a lot in healthcare AI: **never just trust the first answer the LLM gives.** Every response passes through retrieval, confidence scoring, and a dedicated hallucination/safety check before it reaches a user.

---

## 🧠 The Antahkarana Reasoning Pipeline

Inspired by classical cognitive architecture, the pipeline splits reasoning into five single-responsibility stages:

```
   Question / Document
          │
          ▼
   ┌─────────────┐
   │   MANAS     │  Routes by type (dosage / interaction / lab / scan / general)
   │  (Routing)  │  + detects Tamil vs English
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │   CHITTA    │  Vector retrieval (Qdrant) over medical knowledge
   │    (RAG)    │  → falls back to live web search if context is thin
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │   BUDDHI    │  LLM reasoning (Groq LLaMA 3.3 70B)
   │  (Engine)   │  → 3-model fallback chain for reliability
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │  AHAMKARA   │  Scores answer confidence (0–100)
   │ (Confidence)│  based on context quality + verification
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │   SAKSHI    │  Detects hallucination patterns
   │  (Safety)   │  → enforces medical disclaimers before output
   └──────┬──────┘
          ▼
      Final Answer
```

| Module | File | Responsibility |
|---|---|---|
| 🧭 Manas | `engine/manas.py` | Question routing + language detection |
| 📚 Chitta | `engine/chitta.py` | RAG retrieval via Qdrant, web-search fallback |
| 🧠 Buddhi | `engine/buddhi.py` | LLM reasoning with multi-model fallback |
| 📊 Ahamkara | `engine/ahamkara.py` | Confidence scoring (0–100) |
| 🛡️ Sakshi | `engine/sakshi.py` | Hallucination detection + safety disclaimers |

📜 **Patent Filed:** App No. 202641043947 — *Antahkarana AI Reasoning Framework*

---

## 📁 Project Structure

```
backend/
├── main.py              # FastAPI app, all route definitions
├── auth/                 # Firebase phone OTP auth
├── compliance/           # Safety layer, DPDP-aligned data handling
├── db/                   # Supabase client
├── engine/               # Manas · Chitta · Buddhi · Ahamkara · Sakshi
├── rag/                  # Vector retrieval logic
├── tools/                # Dosage calculator, medicine lookup, TTS, web search
├── vision/               # Lab report / scan / medicine image analysis
└── tests/                # Smoke tests (CI-safe, heavy deps stubbed)

infrastructure/           # Terraform IaC for Azure Container Apps
monitoring/                # Prometheus + Grafana stack
Dockerfile
```

---

## 🔌 API Overview

| Endpoint | Method | Purpose |
|---|---|---|
| `/health`, `/ready` | GET | Liveness / readiness probes |
| `/api/auth/send-otp`, `/api/auth/verify-otp` | POST | Firebase phone OTP login flow |
| `/api/auth/firebase-session` | POST | Establish authenticated session |
| `/api/analyze` | POST | Core endpoint — text + optional image, routed through the Antahkarana pipeline |
| `/api/reason` | POST | Direct reasoning query |
| `/api/dosage` | POST | Dosage calculation tool |
| `/api/drug-interaction` | POST | Drug interaction check |
| `/api/tts` | POST | Tamil text-to-speech (Sarvam) |
| `/api/sources` | GET | Citations/sources for a given answer |
| `/api/user/history`, `/api/user/status` | GET | User chat history & account status |
| `/api/consent` | POST | DPDP-aligned consent recording |
| `/api/data/delete` | DELETE | OTP-verified, permanent account/data deletion |
| `/api/grievance` | POST | Grievance/complaint submission |

> Full request/response schemas are defined with Pydantic models in `main.py`.

---

## 🛠 Tech Stack

<table>
<tr>
<td valign="top" width="50%">

**Core**
- FastAPI + Uvicorn (4 workers)
- Pydantic v2
- Python 3.11

**AI / LLM**
- Groq — LLaMA 3.3 70B (+ fallback models)
- OpenAI GPT-4o Vision (image analysis)
- Sentence-Transformers (embeddings)
- Google Gemini (`google-genai`)

**Vision & Documents**
- PyMuPDF, pdfplumber (PDF parsing)
- Pillow, torch/torchvision (CPU)

</td>
<td valign="top" width="50%">

**Storage & Retrieval**
- Qdrant (vector DB for RAG)
- ChromaDB
- Supabase (PostgreSQL)
- Redis (OTP / rate-limit cache)

**Auth & Safety**
- Firebase Admin (phone OTP)
- SlowAPI (rate limiting)
- Custom compliance/safety layer (DPDP Act 2023)

**Ops**
- Docker
- Azure Container Apps
- Terraform (IaC)
- Prometheus + Grafana

</td>
</tr>
</table>

---

## 🚀 Getting Started

```bash
git clone https://github.com/rajaganaa/anbu-health-ai-api.git
cd anbu-health-ai-api/backend

pip install -r requirements.txt
```

Create a `.env` file with your API keys (Groq, OpenAI, Qdrant, Supabase, Firebase, etc. — see `infrastructure/README.md` for the full variable list). Then run:

```bash
uvicorn main:app --reload --port 8000
```

API will be live at `http://localhost:8000` — check `/health` and `/docs` (FastAPI auto-generated Swagger UI).

### Run tests

```bash
pip install pytest httpx
pytest tests/ -v
```

Smoke tests stub out heavy dependencies (torch, Groq, Qdrant, Firebase, etc.) so the suite runs in seconds without real API keys — useful for CI.

---

## 🐳 Docker

```bash
docker build -t anbu-health-ai-api .
docker run -p 8000:8000 --env-file .env anbu-health-ai-api
```

Includes a built-in healthcheck hitting `/health` every 30s.

---

## ☁️ Infrastructure

Deployed on **Azure Container Apps**, provisioned via Terraform in `infrastructure/`. The stack covers the resource group, container app environment, and app definition — see `infrastructure/README.md` for setup and import steps.

CI/CD: GitHub Actions builds the Docker image and deploys on push to `main`.

---

## 📊 Monitoring

Prometheus + Grafana stack in `monitoring/`, instrumented via `prometheus-fastapi-instrumentator` for request latency, error rates, and throughput on every endpoint.

```bash
cd monitoring
docker-compose up
```

---

## 🔒 Compliance & Safety

- **DPDP Act 2023 aligned** — explicit consent capture, OTP-verified data deletion, grievance redressal endpoint
- **Sakshi safety layer** — every AI answer is scanned for hallucination patterns (e.g. absolute claims like "100% cure" or "no side effects") before reaching the user
- **Medical disclaimer enforcement** — every response is paired with a clear "consult your doctor" disclaimer in the user's detected language

---

## 👤 Author

**Rajaganapathy M**
Founder, AI Vision (MSME Registered) · M.Tech AI (CGPA 9.6/10) · Patent Filed · IEEE Paper Submitted

🌍 [Portfolio](https://rajaganaa.github.io) · 🤗 [Hugging Face](https://huggingface.co/RajGana) · 🆔 ORCID: 0009-0006-9701-7942

---

<div align="center">
<sub>⚠️ This system provides AI-generated health information only and does not replace professional medical advice. Always consult a doctor.</sub>
</div>
