"""Regenerate the verification artifacts from a real run.

The evidence files in the repo root used to be pasted transcripts. Nothing
regenerated them and nothing failed when they went stale, so test-results.txt sat
at 83 tests long after the suite had grown past it. A stale PASS is worse than no
PASS, because it is read as evidence.

This script produces those files from an actual run and stamps them with the
commit and the time, so a claim can be checked against the code it describes.

    python tools/verify.py            # tests, then rewrite the artifacts
    python tools/verify.py --check    # fail if the artifacts are out of date

It never opens a serial port, and it never homes, jogs, frames or fires anything.
Hardware evidence stays a separate, deliberately manual step.
"""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "test-results.txt"
REPORT = ROOT / "verification-report.json"


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def git(*args):
    result = run(["git", *args])
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def run_tests():
    result = run([sys.executable, "-m", "pytest", "-q"])
    tail = [line for line in result.stdout.splitlines() if line.strip()]
    summary = tail[-1] if tail else "no output"
    match = re.search(r"(\d+) passed", summary)
    failed = re.search(r"(\d+) failed", summary)
    return {
        "command": "python -m pytest -q",
        "passed": int(match[1]) if match else 0,
        "failed": int(failed[1]) if failed else 0,
        "summary": summary,
        "exit_code": result.returncode,
    }


def bundled_fixture(path):
    """Confirm the packaged binary carries the machine profile it verifies against.

    A one-file build without --add-data still launches, so this cannot be checked
    by watching the process stay alive. Read the archive instead.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return {"checked": False, "reason": "PyInstaller not installed"}
    try:
        archive = CArchiveReader(str(path))
        entry = next((name for name in archive.toc if "observed.txt" in name), None)
        if entry is None:
            return {"checked": True, "present": False,
                    "reason": "observed.txt missing; the app cannot verify the profile"}
        text = archive.extract(entry)
        text = text.decode("ascii", "replace") if isinstance(text, (bytes, bytearray)) else str(text)
        return {
            "checked": True,
            "present": True,
            "entry": entry,
            "firmware_line": "[VER:V1.055.Oct 13 2023:]" in text,
            "bed_setting": "$130=365.000" in text,
        }
    except Exception as exc:
        return {"checked": False, "reason": f"{type(exc).__name__}: {exc}"}


def executables():
    found = {}
    for path in sorted(ROOT.glob("AtomstackController*.exe")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        found[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": digest,
            "fixture": bundled_fixture(path),
        }
    return found


def build_report():
    tests = run_tests()
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "tree_clean": git("status", "--porcelain") == "",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "tests": tests,
        "executables": executables(),
        "hardware": {
            "performed": False,
            "note": ("No port opened by this script. Real-machine evidence is a "
                     "separate manual step and covers read-only status only."),
        },
    }


def render(report):
    tests = report["tests"]
    verdict = "PASS" if tests["exit_code"] == 0 else "FAIL"
    lines = [
        f"{verdict}: {tests['passed']} tests passed, {tests['failed']} failed.",
        f"Generated {report['generated']} from commit {report['commit']} "
        f"on branch {report['branch']}.",
        f"Working tree clean: {'yes' if report['tree_clean'] else 'no'}.",
        f"Python {report['python']} on {report['platform']}.",
        f"pytest summary: {tests['summary']}",
        "",
    ]
    if report["executables"]:
        lines.append("Packaged executables found in the delivery folder. This script")
        lines.append("does not build or launch them, so these hashes identify whatever")
        lines.append("is on disk and do NOT prove it was built from the commit above:")
        for name, meta in report["executables"].items():
            lines.append(f"  {name}  {meta['bytes']} bytes  sha256 {meta['sha256']}")
            fixture = meta["fixture"]
            if not fixture.get("checked"):
                lines.append(f"    fixture bundle: not checked ({fixture.get('reason')})")
            elif fixture.get("present") and fixture.get("firmware_line") and fixture.get("bed_setting"):
                lines.append("    fixture bundle: observed.txt present, V1.055 and $130=365.000 intact")
            else:
                lines.append(f"    fixture bundle: FAIL {fixture}")
    else:
        lines.append("No packaged executable present. Build one before delivery.")
    lines += [
        "",
        "No serial port was opened. This script never homes, jogs, frames or fires.",
        "Regenerate with: python tools/verify.py",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the artifacts do not match a fresh run")
    args = parser.parse_args()

    report = build_report()
    text = render(report)
    payload = json.dumps(report, indent=2) + "\n"

    if args.check:
        current = RESULTS.read_text(encoding="utf-8") if RESULTS.exists() else ""
        stale = [
            line for line in (current.splitlines()[:1] or [""])
            if line != text.splitlines()[0]
        ]
        if report["tests"]["exit_code"] != 0:
            print(text)
            print("Tests failed.")
            return 1
        if stale:
            print("Verification artifacts are out of date.")
            print(f"  recorded: {current.splitlines()[0] if current else '(missing)'}")
            print(f"  actual:   {text.splitlines()[0]}")
            print("Run: python tools/verify.py")
            return 1
        print("Verification artifacts match a fresh run.")
        return 0

    RESULTS.write_text(text, encoding="utf-8")
    REPORT.write_text(payload, encoding="utf-8")
    print(text)
    print(f"Wrote {RESULTS.name} and {REPORT.name}.")
    return report["tests"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
