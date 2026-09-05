"""Build the packaged Windows executable from source.

The delivered .exe used to have no tracked recipe: PyInstaller was not pinned, no
.spec was in the repo, and nothing recorded which commit a binary came from. A
20 MB artifact that cannot be rebuilt is not deliverable, it is a souvenir.

    python tools/build.py                # test, then build
    python tools/build.py --skip-tests   # build only
    python tools/build.py --allow-dirty  # build with uncommitted changes

The build refuses to run on a dirty tree by default, because a binary that cannot
be traced to a commit is exactly the problem this script exists to remove.

The fixture at atomstack/fixtures/observed.txt has to be bundled explicitly. It
is read through Path(__file__).parent at runtime, which resolves inside the
one-file extraction directory, so without --add-data the packaged app cannot
verify the machine profile and refuses to arm.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atomstack import __version__

FIXTURE = "atomstack/fixtures/observed.txt"


def run(command, **kwargs):
    print("+ " + " ".join(str(part) for part in command))
    return subprocess.run(command, cwd=ROOT, **kwargs)


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    dirty = git("status", "--porcelain") != ""
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    if dirty and not args.allow_dirty:
        print("Working tree has uncommitted changes.")
        print("Commit them, or pass --allow-dirty to build an untraceable binary.")
        return 1

    if not args.skip_tests:
        if run([sys.executable, "-m", "pytest", "-q"]).returncode != 0:
            print("Tests failed. Not building.")
            return 1

    name = f"AtomstackController-{__version__}"
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", name,
        "--add-data", f"{FIXTURE}{separator}atomstack/fixtures",
        "main.py",
    ]
    if run(command).returncode != 0:
        print("PyInstaller failed.")
        return 1

    built = ROOT / "dist" / f"{name}.exe"
    if not built.exists():
        print(f"Expected {built} but it is missing.")
        return 1

    versioned = ROOT / f"{name}.exe"
    current = ROOT / "AtomstackController.exe"
    for destination in (versioned, current):
        try:
            shutil.copy2(built, destination)
        except PermissionError:
            print()
            print(f"Cannot overwrite {destination.name}: it is open in another process.")
            print("Close the running controller, then re-run this script. The fresh")
            print(f"binary is already at {built.relative_to(ROOT)} if you need it now.")
            print()
            print("Find the process with:")
            print('  Get-Process AtomstackController | Select-Object Id, Path')
            return 1

    digest = sha256(versioned)
    print()
    print(f"Built {name}.exe from commit {commit}{' (dirty)' if dirty else ''}")
    print(f"  {versioned.stat().st_size} bytes")
    print(f"  sha256 {digest}")
    print(f"  copied to {current.name}")
    print()
    print("Next: launch it, choose Open simulator, and confirm no port opens.")
    print("Then run python tools/verify.py to record the result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
