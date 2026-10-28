import io

from docx import Document

from tests.conftest import BASE_SEGMENTS

Q_MAND = {"id": "q-1", "question": "Who approves refunds above ₹5,000?", "reason": "r",
          "category": "approval_rule", "severity": "mandatory", "related_item_ids": ["step-3"], "origin": "rule"}
Q_OPT = {"id": "q-2", "question": "Is an email receipt sent?", "reason": "r", "category": "other",
         "severity": "optional", "related_item_ids": [], "origin": "llm"}


def payload(sop, answers, fmt, name="Meera"):
    sop["questions"] = [Q_MAND, Q_OPT]
    sop["checklist"] = [{"id": "chk-1", "text": "Confirm: Who approves refunds above ₹5,000?"},
                        {"id": "chk-2", "text": "All steps tested end-to-end"}]
    return {"sop": sop, "answers": answers, "reviewer_name": name, "format": fmt, "segments": BASE_SEGMENTS}


ANSWERED = [{"question_id": "q-1", "question": Q_MAND["question"], "answer": "The store manager."}]


def docx_text(content):
    doc = Document(io.BytesIO(content))
    return [p.text for p in doc.paragraphs]


def test_export_docx_valid_sections(client, sop):
    r = client.post("/api/export", json=payload(sop, ANSWERED, "docx"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'filename="issue-a-customer-refund.docx"' in r.headers["content-disposition"]
    paras = docx_text(r.content)
    joined = "\n".join(paras)
    for heading in ("Purpose", "Prerequisites", "Steps", "Warnings", "Checklist", "Answered questions",
                    "Appendix: Sources"):
        assert heading in paras
    assert paras[0] == "Issue a customer refund"
    assert any(p.startswith("Reviewed by Meera on ") for p in paras)
    assert "DRAFT" not in joined
    assert "3. Decision – if refund is above ₹5,000: Get manager approval before refunding." in paras
    assert "☐ All steps tested end-to-end" in paras
    assert "Who approves refunds above ₹5,000? — The store manager." in paras
    assert any("S1-0001" in p and "admin dashboard" in p for p in paras)
    assert len(r.content) < 4_500_000


def test_export_draft_watermark_when_mandatory_open(client, sop):
    r = client.post("/api/export", json=payload(sop, [], "docx"))
    paras = docx_text(r.content)
    assert paras[0] == "DRAFT – NOT VERIFIED"
    assert "Appendix: Open questions" in paras
    assert "[mandatory] Who approves refunds above ₹5,000?" in paras
    assert not any(p.startswith("Reviewed by") for p in paras)


def test_export_markdown(client, sop):
    r = client.post("/api/export", json=payload(sop, ANSWERED, "md", name=""))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert md.startswith("# Issue a customer refund")
    assert "_Reviewed by unnamed reviewer on " in md
    assert "## Steps" in md and "1. Log in to the admin dashboard." in md
    assert "- ☐ All steps tested end-to-end" in md
    assert "S1-0006" in md and "## Appendix: Open questions" in md  # optional q-2 still open
    r = client.post("/api/export", json=payload(sop, [], "md"))
    assert r.text.startswith("# DRAFT – NOT VERIFIED")
    sop["steps"][0]["action"] = "Call ravi@example.com"
    r = client.post("/api/export", json=payload(sop, ANSWERED, "md"))
    assert "ravi@example.com" not in r.text and "[EMAIL]" in r.text
    assert client.post("/api/export", json={"format": "pdf"}).status_code == 422
