import copy
import os
import socket

import pytest

for _k in ("GROQ_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "VISION_MODEL",
           "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL"):
    os.environ.pop(_k, None)
os.environ["RATE_LIMIT_MAX"] = "100000"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded_connect(self, address, *a, **k):
        # Windows asyncio uses a loopback socketpair internally; everything else is blocked.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *a, **k)
        raise RuntimeError("Network access is blocked in tests")

    def guard(*_a, **_k):
        raise RuntimeError("Network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guard)


def seg(sid, text, kind="note_line", role="staff", start=None):
    return {"id": sid, "source_index": int(sid[1:].split("-")[0]), "kind": kind, "speaker_role": role,
            "text": text, "start_s": start, "end_s": start, "confidence": 1.0}


BASE_SEGMENTS = [
    seg("S1-0001", "Log in to the admin dashboard with your staff account."),
    seg("S1-0002", "Open the Orders page and search for the order number."),
    seg("S1-0003", "Refunds above ₹5,000 need manager approval."),
    seg("S1-0004", 'Click the "Issue refund" button and enter the refund amount.'),
    seg("S1-0005", "If the payment gateway shows an error, retry once and then escalate to finance."),
    seg("S1-0006", "Check that the order status changes to Refunded."),
]


def ref(sid, quote):
    return {"segment_id": sid, "quote": quote}


def step(n, action, refs, kind="action", condition=None, expected=None, ui=None, status="supported"):
    return {"id": f"step-{n}", "number": n, "kind": kind, "actor": "Support agent", "action": action,
            "condition": condition, "expected_result": expected, "ui_elements": ui or [],
            "source_refs": refs, "status": status}


BASE_SOP = {
    "title": "Issue a customer refund",
    "purpose": "Refund a customer order from the admin dashboard.",
    "prerequisites": [{"id": "pre-1", "text": "Staff account with access to the admin dashboard",
                       "source_refs": [ref("S1-0001", "admin dashboard with your staff account")],
                       "status": "supported"}],
    "steps": [
        step(1, "Log in to the admin dashboard.", [ref("S1-0001", "Log in to the admin dashboard")]),
        step(2, "Open the Orders page and search for the order number.",
             [ref("S1-0002", "Open the Orders page")], ui=["Orders"]),
        step(3, "Get manager approval before refunding.", [ref("S1-0003", "need manager approval")],
             kind="decision", condition="refund is above ₹5,000"),
        step(4, "Click Issue refund and enter the refund amount.",
             [ref("S1-0004", "enter the refund amount")], ui=["Issue refund"]),
        step(5, "Check that the order status changes to Refunded.",
             [ref("S1-0006", "order status changes to Refunded")], kind="verification",
             expected="Order status is Refunded"),
    ],
    "warnings": [{"id": "warn-1", "text": "If the payment gateway shows an error, retry once and then escalate.",
                  "severity": "critical", "source_refs": [ref("S1-0005", "retry once and then escalate")],
                  "status": "supported"}],
    "questions": [],
    "checklist": [],
}


@pytest.fixture
def segments():
    return copy.deepcopy(BASE_SEGMENTS)


@pytest.fixture
def sop():
    return copy.deepcopy(BASE_SOP)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    app.dependency_overrides.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def use_llm(client):
    from app import ai
    from app.main import app

    def _set(fake):
        app.dependency_overrides[ai.get_llm] = lambda: fake
        return fake

    return _set
