"""Run ruff, pytest with coverage, and evals; write reports/TEST_REPORT.md."""

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ruff_code, ruff_out = run([sys.executable, "-m", "ruff", "check", "."])
    test_code, test_out = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--cov=app",
                               "--cov-report=term", "--cov-fail-under=80"])
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", test_out)
    coverage = int(m.group(1)) if m else 0
    passed = int((re.search(r"(\d+) passed", test_out) or [0, 0])[1])
    failed = int((re.search(r"(\d+) failed", test_out) or [0, 0])[1])
    errors = int((re.search(r"(\d+) error", test_out) or [0, 0])[1])

    from evals.run_evals import run_all

    evals = run_all()
    evals_ok = all(e["passed"] for e in evals)
    ok = ruff_code == 0 and test_code == 0 and coverage >= 80 and evals_ok

    lines = [
        "# Test report", "", f"Generated: {dt.datetime.now().isoformat(timespec='seconds')}", "",
        f"**Overall: {'PASS' if ok else 'FAIL'}**", "",
        "| Check | Result |", "|---|---|",
        f"| ruff | {'pass' if ruff_code == 0 else 'FAIL'} |",
        f"| pytest | {passed} passed, {failed} failed, {errors} errors |",
        f"| coverage (app/) | {coverage}% (min 80%) |",
        f"| evals | {sum(e['passed'] for e in evals)}/{len(evals)} passed |", "",
        "## Evals", "",
    ]
    for e in evals:
        lines.append(f"- {'PASS' if e['passed'] else 'FAIL'} `{e['case']}`")
        lines += [f"  - {p}" for p in e["problems"]]
    lines += ["", "## Ruff", "", "```", ruff_out.strip() or "ok", "```", "", "## Pytest", "", "```",
              test_out.strip()[-6000:], "```", ""]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "TEST_REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"ruff: {'pass' if ruff_code == 0 else 'FAIL'}")
    if ruff_code:
        print(ruff_out)
    print(f"pytest: {passed} passed, {failed} failed, {errors} errors")
    if test_code and not failed:
        print(test_out[-3000:])
    print(f"coverage: {coverage}%")
    for e in evals:
        print(f"eval {'PASS' if e['passed'] else 'FAIL'} {e['case']}" + "".join(f"\n  - {p}" for p in e["problems"]))
    print(f"OVERALL: {'PASS' if ok else 'FAIL'}  (report: reports/TEST_REPORT.md)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
