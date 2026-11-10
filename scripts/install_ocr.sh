#!/usr/bin/env bash
# Optional: set up OCR (Tesseract with English, French and Japanese) for the CCIFJ Concierge.
#
# You can skip this. The chat, the agents and the dashboard work without OCR; it is only used to
# read scanned PDFs and images. Text PDFs, Word files and text files never need it.
#
#   bash scripts/install_ocr.sh         install what is missing, then check
#   bash scripts/install_ocr.sh --check  only report what is installed; change nothing
#
# What it does:
#   1. Installs the Tesseract program with your system's package manager (Homebrew, apt, dnf, yum,
#      pacman, zypper or apk). On Windows (Git Bash) it hands over to install_ocr.ps1.
#   2. If the three languages are not available, downloads their files from the official
#      tesseract-ocr/tessdata_fast repository into var/tessdata inside this project. That needs no
#      admin rights and works the same on every system; the concierge looks there by itself.
#   3. Checks the result. If a step cannot be done, it says what to run by hand and why.
#
# It never changes the concierge itself, and it is safe to run again.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESSDATA="$ROOT/var/tessdata"
LANGS="eng fra jpn"
DATA_URL="https://github.com/tesseract-ocr/tessdata_fast/raw/main"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mnote:\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

optional_note() {
  echo
  echo "OCR is optional. The concierge works without it; only scanned PDFs and images are not read."
}

# The languages Tesseract can use right now: its own, or the ones in var/tessdata.
missing_languages() {
  local listed missing=""
  if [ -f "$TESSDATA/eng.traineddata" ] && [ -f "$TESSDATA/fra.traineddata" ] && [ -f "$TESSDATA/jpn.traineddata" ]; then
    listed="$(TESSDATA_PREFIX="$TESSDATA" tesseract --list-langs 2>&1)"
  else
    listed="$(tesseract --list-langs 2>&1)"
  fi
  for lang in $LANGS; do
    printf '%s\n' "$listed" | grep -qx "$lang" || missing="$missing $lang"
  done
  printf '%s' "${missing# }"
}

report() {
  if ! have tesseract; then
    echo "Tesseract: not installed"
    return 1
  fi
  echo "Tesseract: $(tesseract --version 2>&1 | head -n 1) ($(command -v tesseract))"
  local missing
  missing="$(missing_languages)"
  if [ -z "$missing" ]; then
    echo "Languages: English, French and Japanese are available"
    return 0
  fi
  echo "Languages missing: $missing"
  return 1
}

as_root() {
  if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then "$@"
  elif have sudo; then sudo "$@"
  else
    warn "This step needs administrator rights and 'sudo' is not available. Run as root: $*"
    return 1
  fi
}

install_program() {
  case "$(uname -s)" in
    Darwin)
      if have brew; then say "Installing Tesseract with Homebrew"; brew install tesseract
      else
        warn "Homebrew is not installed. Install it from https://brew.sh, then run this script again,"
        warn "or install Tesseract another way and run this script again for the languages."
        return 1
      fi ;;
    Linux)
      if   have apt-get; then say "Installing Tesseract with apt"; as_root apt-get update -y && as_root apt-get install -y tesseract-ocr
      elif have dnf;     then say "Installing Tesseract with dnf"; as_root dnf install -y tesseract
      elif have yum;     then say "Installing Tesseract with yum"; as_root yum install -y tesseract
      elif have pacman;  then say "Installing Tesseract with pacman"; as_root pacman -S --noconfirm --needed tesseract
      elif have zypper;  then say "Installing Tesseract with zypper"; as_root zypper --non-interactive install tesseract-ocr
      elif have apk;     then say "Installing Tesseract with apk"; as_root apk add --no-cache tesseract-ocr
      else
        warn "No supported package manager was found. Install the 'tesseract' package for your system,"
        warn "then run this script again for the languages."
        return 1
      fi ;;
    *)
      warn "This system is not recognised: $(uname -s). Install Tesseract yourself, then run this script again."
      return 1 ;;
  esac
}

download() {  # download <url> <target>
  if have curl; then curl -fsSL --retry 2 -o "$2" "$1"
  elif have wget; then wget -q -O "$2" "$1"
  else warn "Neither curl nor wget is installed, so the language files cannot be downloaded."; return 1
  fi
}

install_languages() {
  say "Downloading English, French and Japanese into var/tessdata (under 10 MB, no admin rights needed)"
  mkdir -p "$TESSDATA" || { warn "Cannot create $TESSDATA"; return 1; }
  for lang in $LANGS; do
    if [ -s "$TESSDATA/$lang.traineddata" ]; then continue; fi
    if ! download "$DATA_URL/$lang.traineddata" "$TESSDATA/$lang.traineddata.part"; then
      rm -f "$TESSDATA/$lang.traineddata.part"
      warn "Could not download $lang.traineddata. Check the internet connection, or download it from"
      warn "$DATA_URL/$lang.traineddata and put it in $TESSDATA"
      return 1
    fi
    mv "$TESSDATA/$lang.traineddata.part" "$TESSDATA/$lang.traineddata"
  done
}

# ---- Windows from Git Bash: use the PowerShell installer ----
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    args=()
    [ "$CHECK_ONLY" = "1" ] && args=(-Check)
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT/scripts/install_ocr.ps1" "${args[@]}" ;;
esac

if [ "$CHECK_ONLY" = "1" ]; then
  report && exit 0
  optional_note
  exit 1
fi

if report; then
  echo "Nothing to do: OCR is ready."
  exit 0
fi

if ! have tesseract; then
  install_program || { optional_note; exit 1; }
  have tesseract || { warn "Tesseract was installed but is not on the PATH yet. Open a new terminal and run this script again."; optional_note; exit 1; }
fi

if [ -n "$(missing_languages)" ]; then
  install_languages || { optional_note; exit 1; }
fi

echo
if report; then
  say "OCR is ready. Restart the concierge if it is running."
  exit 0
fi
warn "OCR is still not complete; see the lines above."
optional_note
exit 1
