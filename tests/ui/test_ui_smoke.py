"""Dev-only browser smoke test (skipped when Playwright isn't installed).

Runs the real page against the API in fake-AI mode: Try a sample -> Review -> answer -> export Markdown.
Saves screenshots for 3 widths x 2 themes to reports/ui/. External requests (fonts) are stubbed.
"""

import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "reports" / "ui"
WIDTHS = {"360": (360, 780), "768": (768, 1000), "1280": (1280, 900)}


@pytest.fixture(scope="module")
def base_url():
    import uvicorn

    from app.main import app

    old = os.environ.get("FAKE_AI")
    os.environ["FAKE_AI"] = "1"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
    if old is None:
        os.environ.pop("FAKE_AI", None)
    else:
        os.environ["FAKE_AI"] = old


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browsers not downloaded
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def new_page(browser, base_url, width, theme, errors):
    w, h = WIDTHS[width]
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme, reduced_motion="reduce",
                              accept_downloads=True)
    page = ctx.new_page()
    page.route(re.compile(r"^https?://(?!127\.0\.0\.1).*"),
               lambda route: route.fulfill(status=200, body="", content_type="text/css"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(base_url)
    page.wait_for_selector("#hero h1")
    return ctx, page


def no_horizontal_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def run_sample(page):
    page.click("#sample-btn")
    page.wait_for_selector("#qpanel .qcard", state="attached", timeout=20000)


def test_ocr_is_lazy_and_filters_low_confidence(browser, base_url):
    errors: list[str] = []
    ctx, page = new_page(browser, base_url, "1280", "light", errors)
    assert page.evaluate("!window.Tesseract && !document.querySelector('script[src*=tesseract]')")
    page.click("#start-btn")
    page.click("#tab-screen")
    assert page.locator("#ocr-lang").is_visible()
    assert page.locator("#ocr-lang option").all_inner_texts() == ["English", "English + Hindi", "English + Gujarati"]
    out = page.evaluate("""async () => {
      const m = await import('/ocr.js');
      return [m.cleanResult({lines: [{text: ' Issue  refund ', confidence: 88}, {text: 'zz', confidence: 99},
                                     {text: 'Blurry text', confidence: 31}]}),
              m.cleanResult({lines: [{text: 'noise', confidence: 12}]})];
    }""")
    assert out == [{"text": "Issue refund", "confidence": 88}, None]
    assert page.evaluate("!window.Tesseract")  # importing the module alone doesn't fetch Tesseract
    ctx.close()
    assert not errors, errors


def test_sample_flow_answer_and_export(browser, base_url):
    errors: list[str] = []
    ctx, page = new_page(browser, base_url, "1280", "light", errors)
    run_sample(page)
    assert page.locator(".step").count() >= 4
    assert page.locator(".qcard.mandatory").count() >= 1

    card = page.locator(".qcard").first
    card.locator("textarea").fill("The store manager approves; staff log in with their own admin account.")
    card.get_by_role("button", name="Save").click()
    page.wait_for_selector(".qcard.answered")
    assert page.locator("#regen").is_enabled()

    page.locator(".step .evidence summary").first.click()
    assert page.locator(".step .evidence[open] q").first.is_visible()

    page.get_by_role("button", name="Continue to export").click()
    page.fill("#reviewer", "Priya")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download .md").click()
    text = Path(dl.value.path()).read_text(encoding="utf-8")
    assert dl.value.suggested_filename.startswith("SOP-") and dl.value.suggested_filename.endswith("-v1.md")
    assert "## Steps" in text and "The store manager approves" in text
    page.wait_for_selector(".toast >> text=Downloaded")
    ctx.close()
    assert not errors, errors


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", list(WIDTHS))
def test_screenshots(browser, base_url, width, theme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    ctx, page = new_page(browser, base_url, width, theme, errors)
    tag = f"{width}-{theme}"
    page.screenshot(path=str(SHOTS / f"hero-{tag}.png"), full_page=True)
    assert no_horizontal_scroll(page), "hero scrolls horizontally"

    run_sample(page)
    page.screenshot(path=str(SHOTS / f"review-{tag}.png"), full_page=True)
    assert no_horizontal_scroll(page), "review scrolls horizontally"
    if page.locator("#fab").is_visible():
        page.click("#fab")
        page.wait_for_timeout(100)
        page.screenshot(path=str(SHOTS / f"review-sheet-{tag}.png"))
        page.locator(".sheet-close").click()

    page.click('#stepper button[data-step="1"]')
    page.screenshot(path=str(SHOTS / f"sources-{tag}.png"), full_page=True)
    assert no_horizontal_scroll(page), "sources scrolls horizontally"

    page.click('#stepper button[data-step="4"]')
    page.screenshot(path=str(SHOTS / f"export-{tag}.png"), full_page=True)
    assert no_horizontal_scroll(page), "export scrolls horizontally"
    ctx.close()
    assert not errors, errors
