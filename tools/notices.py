"""Generate THIRD-PARTY-NOTICES.md from what is actually installed and bundled.

Permissive licences are not free of obligations: BSD and MIT both require the
copyright notice and the licence text to travel with a binary distribution. A
hand-written notice file goes stale the first time a dependency changes, and a
stale notice is worse than none, so this reads the licence texts out of the
installed packages instead of repeating them.

    python tools/notices.py            # write THIRD-PARTY-NOTICES.md
    python tools/notices.py --check    # fail if it is out of date

Native libraries that arrive inside a wheel — OpenBLAS in numpy, Tcl/Tk with
Python — have no Python metadata of their own, so they are listed here by hand
with a pointer to where their full terms live.
"""
import argparse
import sys
from importlib.metadata import distribution, metadata, version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "THIRD-PARTY-NOTICES.md"

# What the application imports, and therefore what PyInstaller bundles.
PACKAGES = ("pyserial", "fonttools", "pillow", "numpy")

# Bundled by the freezer or inside a wheel, with no metadata to read.
NATIVE = (
    ("CPython runtime", "Python Software Foundation License 2.0",
     "https://docs.python.org/3/license.html",
     "The interpreter and standard library, embedded in the executable."),
    ("Tcl/Tk", "Tcl/Tk License (BSD-style)",
     "https://www.tcl.tk/software/tcltk/license.html",
     "The toolkit behind tkinter, bundled as tcl86 and tk86."),
    ("OpenBLAS", "BSD-3-Clause",
     "https://github.com/OpenMathLib/OpenBLAS/blob/develop/LICENSE",
     "Linear algebra kernels shipped inside the numpy wheel."),
    ("zlib", "zlib License",
     "https://zlib.net/zlib_license.html",
     "Compression, reached through Pillow and the Python runtime."),
    ("Microsoft Visual C++ Runtime", "Microsoft Redistributable Licence",
     "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
     "msvcp140.dll, redistributed as permitted for applications built with MSVC."),
    ("PyInstaller bootloader", "GPL-2.0-or-later WITH Bootloader-exception",
     "https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt",
     "The launcher stub. Its exception permits frozen applications to carry any "
     "licence; this one is MIT."),
)


BSD_3_CLAUSE = """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

STANDARD_TEXTS = {"BSD-3-Clause": BSD_3_CLAUSE}


def source_notice(name):
    """Copyright line and SPDX identifier taken from a package's own source.

    Some packages state their terms in a header rather than shipping a licence
    file. pySerial is one, and BSD still requires the notice to travel with a
    binary, so the header is where it has to come from.
    """
    try:
        package = distribution(name)
    except Exception:
        return None, None
    for item in (package.files or [])[:400]:
        if Path(str(item)).suffix != ".py":
            continue
        try:
            head = package.locate_file(item).read_text(encoding="utf-8",
                                                       errors="replace")[:1500]
        except OSError:
            continue
        holder = next((line.strip(" #	") for line in head.splitlines()
                       if "(C)" in line or "Copyright" in line), None)
        spdx = next((line.split(":", 1)[1].strip() for line in head.splitlines()
                     if "SPDX-License-Identifier" in line), None)
        if holder and spdx:
            return holder, spdx
    return None, None


def licence_text(name):
    """The licence as the package ships it, so the notice is not a paraphrase."""
    try:
        package = distribution(name)
    except Exception:
        return None
    candidates = [f for f in (package.files or [])
                  if Path(str(f)).name.upper().startswith(("LICENSE", "COPYING"))
                  and Path(str(f)).suffix.lower() in ("", ".txt", ".md", ".rst")]
    for item in candidates:
        try:
            text = (package.locate_file(item)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text.strip()) > 200:
            return text.strip()
    return None


def declared_licence(name):
    data = metadata(name)
    return (data.get("License-Expression") or data.get("License")
            or next((c.split("::")[-1].strip() for c in data.get_all("Classifier") or []
                     if c.startswith("License")), "see project")).strip()


def build():
    lines = [
        "# Third-party notices",
        "",
        "This file is generated by `python tools/notices.py`. Do not edit it by hand.",
        "",
        "Atomstack Controller itself is under the MIT licence in `LICENSE`. It is",
        "distributed as a single executable that carries the components below, whose",
        "licences require their notices to travel with it.",
        "",
        "Run `AtomstackController.exe --licenses` to print this file from the",
        "application itself.",
        "",
        "## Fonts",
        "",
        "No font is bundled. Text is rendered from fonts installed on the machine",
        "running the application, read from the Windows font directory. Those fonts",
        "remain under their own licences and are not redistributed here.",
        "",
        "## Python packages",
        "",
    ]
    for name in PACKAGES:
        lines += [f"### {name} {version(name)}", "",
                  f"License: {declared_licence(name)}", ""]
        text = licence_text(name)
        if text:
            lines += ["<details><summary>Licence text</summary>", "", "```",
                      text, "```", "", "</details>", ""]
        else:
            holder, spdx = source_notice(name)
            standard = STANDARD_TEXTS.get(spdx or "")
            if holder and standard:
                lines += [f"This package ships no licence file. The notice below is taken "
                          f"from its own source header, with the standard text for the "
                          f"`{spdx}` identifier it declares.", "",
                          "<details><summary>Licence text</summary>", "", "```",
                          holder, "", standard, "```", "", "</details>", ""]
            else:
                lines += [f"Full text ships with the package: see the {name} distribution.", ""]
    lines += ["## Bundled runtimes and native libraries", ""]
    for name, licence, url, note in NATIVE:
        lines += [f"### {name}", "", f"License: {licence}", "", note, "", f"Terms: {url}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the file on disk is out of date")
    arguments = parser.parse_args()
    generated = build()
    if arguments.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != generated:
            print("THIRD-PARTY-NOTICES.md is out of date. Run python tools/notices.py")
            return 1
        print(f"Notices are current, covering {len(PACKAGES) + len(NATIVE)} components.")
        return 0
    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"Wrote {OUTPUT.name}: {len(PACKAGES)} packages, {len(NATIVE)} bundled components.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
