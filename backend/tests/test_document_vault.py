"""
tests/test_document_vault.py — regression tests for the vision↔Groq context
loss bug.

Background: GPT-4o vision extraction used to live ONLY in the browser tab's
React state (fileVault). The moment that tab reloaded (or the user logged in
on a new session, or switched chats), the in-memory vault was empty, the
backend fell back to mode="general", and Groq answered with zero knowledge of
the document it had just analyzed — even though the user was still asking about
the same report. These tests simulate exactly that sequence (two SEPARATE
requests, as two separate browser sessions would send) against a fake,
in-memory Supabase stand-in, and assert that the second request still has
the first request's extraction available.

Run locally:
    cd backend && pip install pytest httpx
    pytest tests/test_document_vault.py -v
"""
import sys
import types
import pytest

from tests.test_smoke import _stub_heavy_modules  # reuse the existing stub helper


class _FakeDB:
    """In-memory stand-in for db/supabase_client.py."""
    def __init__(self):
        self.rows = []

    def get_token_status(self, phone):
        return {"allowed": True, "count": 0, "remaining": 999, "limit": 1000}

    def increment_token_count(self, phone, tokens_used=0):
        return {"allowed": True}

    def save_message(self, *a, **kw):
        pass

    def log_compliance(self, *a, **kw):
        pass

    def save_document(self, phone, chat_id, file_key, mode, vision_data, file_name=None, ttl_hours=None):
        self.rows = [r for r in self.rows
                     if not (r["phone"] == phone and r["chat_id"] == chat_id and r["file_key"] == file_key)]
        self.rows.append({
            "phone": phone, "chat_id": chat_id, "file_key": file_key,
            "mode": mode, "file_name": file_name, "vision_data": vision_data,
            "created_at": "2026-01-01T00:00:00Z",
        })
        return True

    def get_document_vault(self, phone, chat_id):
        matches = [r for r in self.rows if r["phone"] == phone and r["chat_id"] == (chat_id or "default")]
        return [
            {"file_key": r["file_key"], "mode": r["mode"], "file_name": r["file_name"],
             "vision_data": r["vision_data"], "created_at": r["created_at"]}
            for r in reversed(matches)
        ]

    def get_all_document_vaults(self, phone):
        grouped = {}
        for r in self.rows:
            if r["phone"] == phone:
                grouped.setdefault(r["chat_id"], []).append(r)
        return grouped

    def clear_document_vault(self, phone, chat_id, file_key=None):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not (
            r["phone"] == phone and r["chat_id"] == (chat_id or "default")
            and (file_key is None or r["file_key"] == file_key)
        )]
        return len(self.rows) != before

    def is_enabled(self):
        return True


@pytest.fixture
def client():
    _stub_heavy_modules()
    from fastapi.testclient import TestClient
    import main as main_mod

    captured = {}

    class _FakeManas:
        def route(self, q, mode):
            return {"question_type": "general", "entities": []}

    class _FakeChitta:
        def retrieve(self, q, entities, k=5):
            return {"context_str": "", "sources": [], "num_chunks": 0, "retrieval_method": "test"}

    class _FakeBuddhi:
        def reason(self, **kw):
            captured["vision_info"] = kw.get("vision_info")
            captured["mode"] = kw.get("mode")
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
    main_mod._startup_complete = True

    original_db = main_mod.db
    main_mod.db = _FakeDB()

    def _fake_analyze_image(path, mode):
        if mode == "medicine":
            return {"mode": "medicine", "model": "test-vision",
                    "drug_name": "Paracetamol", "manufacturer": "Cipla Ltd.",
                    "summary": "Paracetamol — pain/fever relief"}
        if mode == "scan":
            return {"mode": "scan", "model": "test-vision",
                    "body_part": "elbow", "scan_type": "X-ray", "scan_provider": "METROPOLIS",
                    "summary": "Elbow fracture, post-surgical"}
        return {
            "mode": mode, "model": "test-vision",
            "tests": [{"name": "HbA1c", "value": "6.5", "unit": "%", "range": "4-5.6", "status": "high"}],
            "abnormal_count": 1, "lab_name": "Test Lab", "patient_name": "Test Patient",
            "summary": "HbA1c slightly high",
        }
    vision_stub = types.ModuleType("vision.anbu_vision")
    vision_stub.analyze_image = _fake_analyze_image
    sys.modules["vision.anbu_vision"] = vision_stub

    tc = TestClient(main_mod.app)
    tc._captured = captured
    tc._fake_db = main_mod.db
    yield tc
    main_mod.db = original_db


def _upload_lab_report(client, phone, chat_id):
    return client.post("/api/analyze", data={
        "question": "என் lab report பாரு",
        "mode": "lab", "phone": phone, "chat_id": chat_id,
    }, files={"image": ("report.jpg", b"fake-bytes", "image/jpeg")})


def test_vision_persists_and_survives_a_simulated_reload(client):
    phone, chat_id = "+919999900001", "chatA"
    r1 = _upload_lab_report(client, phone, chat_id)
    assert r1.status_code == 200
    assert r1.json()["vision"]["lab_name"] == "Test Lab"

    r2 = client.post("/api/analyze", data={
        "question": "இது normal ஆ?", "mode": "general",
        "phone": phone, "chat_id": chat_id,
    })
    assert r2.status_code == 200
    vi = client._captured["vision_info"]
    assert vi is not None, "vision context was lost across the simulated reload"
    assert vi.get("lab_name") == "Test Lab"
    assert vi.get("tests")[0]["name"] == "HbA1c"
    assert client._captured["mode"] == "lab"


def test_document_context_does_not_leak_across_chats(client):
    phone = "+919999900002"
    _upload_lab_report(client, phone, "chatA")

    r = client.post("/api/analyze", data={
        "question": "what about my report?", "mode": "general",
        "phone": phone, "chat_id": "chatB",
    })
    assert r.status_code == 200
    assert client._captured["vision_info"] in (None, {})


def test_anonymous_user_still_works_via_client_sent_vault(client):
    import json
    vault_payload = json.dumps({
        "file-key-123": {"mode": "medicine", "drug_name": "Paracetamol", "summary": "fever/pain relief"}
    })
    r = client.post("/api/analyze", data={
        "question": "side effects?", "mode": "general",
        "file_context": vault_payload,
    })
    assert r.status_code == 200
    assert client._captured["vision_info"]["drug_name"] == "Paracetamol"
    assert client._captured["mode"] == "medicine"


def test_duplicate_entry_from_db_and_client_cache_is_not_double_counted(client):
    import json
    phone, chat_id = "+919999900003", "chatA"
    r1 = _upload_lab_report(client, phone, chat_id)
    file_key = r1.json()["file_key"]

    stale_client_vault = json.dumps({file_key: r1.json()["vision"]})
    r2 = client.post("/api/analyze", data={
        "question": "explain", "mode": "general",
        "phone": phone, "chat_id": chat_id,
        "file_context": stale_client_vault,
    })
    assert r2.status_code == 200
    tests = client._captured["vision_info"].get("tests", [])
    assert len(tests) == 1, f"expected the duplicate to be deduped, got {tests}"


def test_clear_context_endpoint_forgets_the_document(client):
    phone, chat_id = "+919999900004", "chatA"
    _upload_lab_report(client, phone, chat_id)

    r = client.post("/api/chat/clear-context", data={"phone": phone, "chat_id": chat_id})
    assert r.status_code == 200
    assert r.json()["success"] is True

    r2 = client.post("/api/analyze", data={
        "question": "what about my report?", "mode": "general",
        "phone": phone, "chat_id": chat_id,
    })
    assert client._captured["vision_info"] in (None, {})


def test_relevant_document_wins_over_more_recently_uploaded_unrelated_one(client):
    """Reproduces the exact cross-contamination seen in production: a
    medicine photo is uploaded, then later an unrelated X-ray is uploaded
    in the SAME chat, then the user asks a medicine-specific follow-up."""
    phone, chat_id = "+919999900005", "chatA"

    r1 = client.post("/api/analyze", data={
        "question": "இந்த மருந்து என்ன?", "mode": "medicine",
        "phone": phone, "chat_id": chat_id,
    }, files={"image": ("R.jpeg", b"fake-bytes", "image/jpeg")})
    assert r1.status_code == 200

    r2 = client.post("/api/analyze", data={
        "question": "இந்த scan என்ன சொல்கிறது?", "mode": "scan",
        "phone": phone, "chat_id": chat_id,
    }, files={"image": ("xray.jpeg", b"fake-bytes", "image/jpeg")})
    assert r2.status_code == 200

    r3 = client.post("/api/analyze", data={
        "question": "what is the batch number of this paracetamol tablet?",
        "mode": "general", "phone": phone, "chat_id": chat_id,
    })
    assert r3.status_code == 200
    vi = client._captured["vision_info"]
    assert vi is not None
    assert vi.get("mode") == "medicine", f"expected the medicine doc to win, got mode={vi.get('mode')}"
    assert client._captured["mode"] == "medicine"
