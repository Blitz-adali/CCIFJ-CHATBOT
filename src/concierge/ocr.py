"""Finding Tesseract for OCR. OCR is optional: without it only scans and images cannot be read.

Two things make an install work regardless of how the machine is set up:

- the Tesseract program is looked for on the PATH and then in the usual install folders, so a
  Windows install that did not update the PATH still works;
- the English, French and Japanese language files may live inside the project, under
  `var/tessdata`, where `scripts/install_ocr.sh` / `install_ocr.ps1` put them without admin rights.

Overrides: CCIFJ_TESSERACT_CMD (path to the program) and CCIFJ_TESSDATA_DIR (language files).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

LANGUAGES = ("eng", "fra", "jpn")


def local_tessdata() -> Path:
    configured = os.getenv("CCIFJ_TESSDATA_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "var" / "tessdata"


def has_languages(folder: Path) -> bool:
    return all((folder / f"{language}.traineddata").is_file() for language in LANGUAGES)


def _usual_places() -> list[Path]:
    places = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.getenv(variable):
            places.append(Path(os.environ[variable]) / "Tesseract-OCR" / "tesseract.exe")
    if os.getenv("LOCALAPPDATA"):
        places.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    if os.getenv("USERPROFILE"):
        places.append(Path(os.environ["USERPROFILE"]) / "scoop" / "shims" / "tesseract.exe")
    places += [Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract"), Path("/usr/bin/tesseract")]
    return places


def tesseract_command() -> str | None:
    """The Tesseract program to run, or None when it is not installed."""
    configured = os.getenv("CCIFJ_TESSERACT_CMD", "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    return next((str(place) for place in _usual_places() if place.is_file()), None)


@lru_cache(maxsize=1)
def configure() -> str | None:
    """Point pytesseract at the program and, when present, the project's language files.

    Returns the program path, or None when Tesseract is not installed. Safe to call before every
    OCR; the lookup runs once per process.
    """
    command = tesseract_command()
    if command is None:
        return None
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = command
    folder = local_tessdata()
    if has_languages(folder):
        # Tesseract reads its language files from here; nothing needs quoting or admin rights.
        os.environ["TESSDATA_PREFIX"] = str(folder)
    return command


def status() -> dict:
    """What OCR can do on this machine, for the installer's check and for troubleshooting."""
    command = tesseract_command()
    if command is None:
        return {"available": False, "command": None, "languages": [], "missing": list(LANGUAGES), "tessdata": None}
    folder = local_tessdata()
    environment = dict(os.environ)
    if has_languages(folder):
        environment["TESSDATA_PREFIX"] = str(folder)
    try:
        listed = subprocess.run([command, "--list-langs"], capture_output=True, text=True, timeout=30, env=environment)
        languages = [line.strip() for line in (listed.stdout + listed.stderr).splitlines()[1:] if line.strip()]
    except (OSError, subprocess.SubprocessError):
        languages = []
    missing = [language for language in LANGUAGES if language not in languages]
    return {"available": not missing, "command": command, "languages": languages, "missing": missing,
            "tessdata": str(folder) if has_languages(folder) else None}


if __name__ == "__main__":
    report = status()
    if report["available"]:
        print(f"OCR is ready: {report['command']} with English, French and Japanese.")
    elif report["command"]:
        print(f"Tesseract is installed ({report['command']}) but these languages are missing: {', '.join(report['missing'])}.")
    else:
        print("Tesseract is not installed. OCR is optional: only scanned PDFs and images need it.")
    raise SystemExit(0 if report["available"] else 1)
