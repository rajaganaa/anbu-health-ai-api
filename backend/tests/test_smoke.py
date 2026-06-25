"""
tests/test_smoke.py — Anbu Health AI smoke tests

These are intentionally minimal: the goal is to catch the class of bug
that `py_compile` cannot — broken route signatures, decorator-ordering
issues (e.g. the slowapi + Form() interaction), endpoints that 500 on a
basic call, etc. This is NOT a substitute for real unit tests of the
Antahkarana pipeline logic (manas/chitta/buddhi/ahamkara/sakshi) — those
should be added incrementally as you have time, ideally one per module.

Heavy ML/external dependencies (torch, sentence-transformers, qdrant,
groq, openai, firebase, redis, wandb) are stubbed out so this test suite
runs in seconds in CI without needing real API keys or downloading models.
Run locally:
    cd backend && pip install pytest httpx
    pytest tests/ -v
"""
import sys
import types
import pytest


def _stub_heavy_modules():
    """Replace heavy/external modules with empty stand-ins before import."""
    stub_names = [
        "groq", "qdrant_client", "qdrant_client.models", "sentence_transformers",
        "chromadb", "fitz", "pdfplumber", "wandb",
        "prometheus_fastapi_instrumentator", "prometheus_client",
        "firebase_admin", "redis", "openai", "requests",
    ]
    # Stub requests so web_search.py lazy import doesn't fail in CI
    import types as _types
    _req_stub = _types.ModuleType("requests")
    _req_stub.get = lambda *a, **kw: type("R", (), {"json": lambda self: {}, "status_code": 200})()
    sys.modules["requests"] = _req_stub
    for name in stub_names:
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["groq"].Groq = object
    sys.modules["qdrant_client"].QdrantClient = object
    sys.modules["sentence_transformers"].SentenceTransformer = object


@pytest.fixture(scope="module")
def client():
    _stub_heavy_modules()
    from fastapi.testclient import TestClient
    import main as main_mod

    # Stub the Antahkarana pipeline so tests don't need real GROQ_API_KEY /
    # network calls — this suite checks the API contract, not LLM quality.
    class _FakeManas:
        def route(self, q, mode):
            return {"question_type": "general", "entities": []}

    class _FakeChitta:
        def retrieve(self, q, entities, k=5):
            return {"context_str": "", "sources": [], "num_chunks": 0, "retrieval_method": "test"}

    class _FakeBuddhi:
        def reason(self, **kw):
            return {
                "draft_answer": "test answer", "structured_response": {},
                "detected_language": "en", "pass2_fired": False,
                "model": "test", "latency_s": 0.01,
            }

    class _FakeAhamkara:
        def score(self, *a, **kw):
            return {"confidence_score": 80}

    class _FakeSakshi:
        def verify(self, **kw):
            return {
                "verified": True, "final_answer": "test answer",
                "hallucination_flags": [], "medical_disclaimer": "test disclaimer",
            }

    main_mod._pipeline = {
        "manas": _FakeManas(), "chitta": _FakeChitta(), "buddhi": _FakeBuddhi(),
        "ahamkara": _FakeAhamkara(), "sakshi": _FakeSakshi(),
    }
    main_mod._startup_complete = True  # skip real startup warm-up in tests

    return TestClient(main_mod.app)


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_ready_endpoint(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_root_endpoint(client):
    r = client.get("/")
    assert r.status_code == 200


def test_analyze_basic_flow(client):
    r = client.post("/api/analyze", data={"question": "test question", "mode": "general"})
    assert r.status_code == 200
    body = r.json()
    assert body["final_answer"] == "test answer"
    assert "confidence" in body


def test_legacy_reason_endpoint(client):
    """Regression test: /api/reason calls analyze() directly in Python —
    must explicitly pass phone=None/chat_id=None or FastAPI's Form()
    defaults won't resolve correctly outside of a real HTTP request."""
    r = client.post("/api/reason", data={"question": "legacy test question"})
    assert r.status_code == 200
    assert r.json()["final_answer"] == "test answer"


def test_analyze_missing_question_returns_422(client):
    r = client.post("/api/analyze", data={"mode": "general"})
    assert r.status_code == 422


def test_send_otp_rate_limit(client):
    """5/minute limit on send-otp — 6th request in a window should 429."""
    phone = "9000000001"
    statuses = []
    for _ in range(6):
        r = client.post("/api/auth/send-otp", data={"phone": phone})
        statuses.append(r.status_code)
    assert 429 in statuses, f"Expected a 429 among {statuses} — rate limit not enforced"


def test_dosage_endpoint_requires_fields(client):
    r = client.post("/api/dosage", data={})
    assert r.status_code == 422
