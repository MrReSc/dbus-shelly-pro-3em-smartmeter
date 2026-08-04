#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VELIB_DIR="$PROJECT_DIR/ext/velib_python"

if [ ! -f "$VELIB_DIR/vedbus.py" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "Missing velib_python submodule and git is not installed." >&2
    exit 1
  fi
  if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "The local test requires a Git checkout so the velib_python submodule can be initialized." >&2
    exit 1
  fi
  echo "Initializing velib_python submodule..."
  git -C "$PROJECT_DIR" submodule update --init --recursive
fi

if ! command -v python3 >/dev/null 2>&1 || ! command -v dbus-run-session >/dev/null 2>&1; then
  echo "Missing local test dependencies." >&2
  echo "Install them on Fedora with:" >&2
  echo "  sudo dnf install python3-dbus python3-gobject python3-requests dbus-daemon" >&2
  exit 1
fi

TEST_PYTHONPATH="$VELIB_DIR"
if [ -n "${PYTHONPATH:-}" ]; then
  TEST_PYTHONPATH="$TEST_PYTHONPATH:$PYTHONPATH"
fi

if ! PYTHONPATH="$TEST_PYTHONPATH" python3 -c 'import dbus, requests, vedbus; from gi.repository import GLib' >/dev/null 2>&1; then
  echo "Missing local test Python modules." >&2
  echo "Install them on Fedora with:" >&2
  echo "  sudo dnf install python3-dbus python3-gobject python3-requests dbus-daemon" >&2
  exit 1
fi

exec dbus-run-session -- env PYTHONPATH="$TEST_PYTHONPATH" \
  python3 "$PROJECT_DIR/tests/local_venus_test.py"
