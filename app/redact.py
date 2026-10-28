"""PII redaction. Money amounts are deliberately kept."""

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|passcode|pin|otp|token|api[ _-]?key|secret|access[ _-]?key)"
    r"(\s*(?:is|:|=)\s*)(?!\[SECRET\])(\S+)"
)
SECRET_TOKEN = re.compile(r"\b(?:sk-|gsk_|AIza|ghp_|xox[abp]-)[A-Za-z0-9_\-]{16,}")
BEARER = re.compile(r"(?i)\bbearer\s+(?!\[SECRET\])[A-Za-z0-9._\-]{16,}")
CARD = re.compile(r"(?<![\w₹$€])(?:\d[ -]?){12,18}\d(?!\w)")
AADHAAR = re.compile(r"(?<![\w₹$€])(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\w|[ -]\d)")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PHONE = re.compile(r"(?<![\w₹$€.,])(?<!\d[ -])(?:\+\d{1,3}[ -]?)?(?:\(\d{2,5}\)[ -]?)?\d[\d -]{7,14}\d(?![\w]|[.,]\d|[ -]\d)")
CURRENCY_BEFORE = re.compile(r"(?i)(?:₹|\$|€|rs\.?|inr|usd|eur)\s*$")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _money_context(text: str, start: int) -> bool:
    return bool(CURRENCY_BEFORE.search(text[max(0, start - 6):start]))


def redact(text: str) -> str:
    if not text:
        return text
    text = SECRET_TOKEN.sub("[SECRET]", text)
    text = BEARER.sub("Bearer [SECRET]", text)
    text = SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[SECRET]", text)
    text = EMAIL.sub("[EMAIL]", text)

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits) and not _money_context(m.string, m.start()):
            return "[CARD]"
        return m.group(0)

    text = CARD.sub(card, text)
    text = AADHAAR.sub(lambda m: m.group(0) if _money_context(m.string, m.start()) else "[AADHAAR]", text)
    text = PAN.sub("[PAN]", text)

    def phone(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13 and not _money_context(m.string, m.start()):
            return "[PHONE]"
        return m.group(0)

    return PHONE.sub(phone, text)
