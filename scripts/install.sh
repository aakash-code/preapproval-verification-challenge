#!/usr/bin/env bash
# One-click setup for the Pre-Approval Verification Tool.
#
# Safe to run more than once — every step below is idempotent (it checks
# whether the work is already done before doing it). Designed for
# non-technical users: every step prints what it's doing and why, and any
# failure prints a plain-language next step instead of a raw stack trace.
#
# Usage:
#   ./scripts/install.sh
# or double-click install.command in Finder (macOS), which calls this.

set -euo pipefail

# ---- locate the repo root (this script lives in <repo>/scripts/) --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ---- tiny UI helpers ------------------------------------------------------
step() { printf '\n\033[1;34m▶ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$1"; }
fail() {
  printf '\n\033[1;31m✗ Setup could not finish.\033[0m\n%s\n\n' "$1"
  exit 1
}

echo "=============================================="
echo " Pre-Approval Verification Tool — Setup"
echo "=============================================="
echo "This will set up everything needed to run the tool on this computer."
echo "It does not need administrator/sudo access, and it's safe to run again"
echo "later if anything changes."

# ---- 1. Find a suitable Python (3.10+) ------------------------------------
step "Step 1/6: Looking for Python 3.10 or newer"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  fail "Python 3.10 or newer wasn't found on this computer.

  On macOS, the easiest fix is to install Homebrew (https://brew.sh) then run:
      brew install python@3.12
  On Windows or Linux, download it from https://www.python.org/downloads/
  (check the box that adds Python to PATH during install on Windows).

  Once Python is installed, run this script again."
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ok "Using $PYTHON_BIN (Python $PY_VERSION)"

# ---- 2. Create the virtual environment ------------------------------------
step "Step 2/6: Setting up an isolated Python environment (.venv)"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  ok "Already set up — skipping."
else
  "$PYTHON_BIN" -m venv "$REPO_ROOT/.venv" || fail "Could not create .venv.
  If you're on Linux, you may need to install the venv module first, e.g.:
      sudo apt install python3-venv"
  ok "Created .venv"
fi

VENV_PY="$REPO_ROOT/.venv/bin/python"
VENV_PIP="$REPO_ROOT/.venv/bin/pip"

# ---- 3. Install Python dependencies ---------------------------------------
step "Step 3/6: Installing required packages (this can take a minute or two)"

"$VENV_PIP" install --quiet --upgrade pip || fail "Could not update pip. Check your internet connection and try again."
"$VENV_PIP" install --quiet -r "$REPO_ROOT/requirements.txt" \
  || fail "Could not install the required packages. Check your internet connection and try again."
ok "Packages installed"

# ---- 4. Install the browser used for website checks -----------------------
step "Step 4/6: Installing the browser used to check provider websites"

"$REPO_ROOT/.venv/bin/playwright" install chromium \
  || fail "Could not install the Chromium browser used for website checks.
  Check your internet connection and try running this script again."
ok "Browser installed"

# ---- 5. Set up local settings (.env) --------------------------------------
step "Step 5/6: Setting up local settings"

if [ -f "$REPO_ROOT/.env" ]; then
  ok "A .env file already exists — leaving it as-is."
else
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  ok "Created .env from the template."
  warn "The tool works fully without an API key (Automatic engine)."
  warn "To unlock the AI-assisted engine and free-form chat later, open the"
  warn "new .env file and set ANTHROPIC_API_KEY=... — see console.anthropic.com"
fi

# ---- 6. Verify the install actually works ---------------------------------
step "Step 6/6: Verifying the install"

"$VENV_PY" -c "import preapproval, fastapi, playwright, pdfplumber" \
  || fail "The install finished but something isn't working correctly.
  Try deleting the .venv folder and running this script again."
ok "Everything looks good."

echo ""
echo "=============================================="
echo " Setup complete!"
echo "=============================================="
echo ""
echo "To start the app, run:"
echo "  .venv/bin/python -m preapproval serve"
echo "Then open http://127.0.0.1:8000 in your browser."
echo ""

# ---- Offer to start the app right now (interactive terminals only) -------
if [ -t 0 ]; then
  read -r -p "Start the app now? [Y/n] " REPLY
  case "$REPLY" in
    [nN]*) ;;
    *)
      echo ""
      echo "Starting the app — open http://127.0.0.1:8000 in your browser."
      echo "Press Ctrl+C in this window to stop it later."
      echo ""
      exec "$VENV_PY" -m preapproval serve
      ;;
  esac
fi
