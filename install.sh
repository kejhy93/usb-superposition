#!/usr/bin/env bash
# Install usb-superposition for the current user (no root):
#   1. a project venv in .venv (--system-site-packages, so a system pyudev is reused)
#   2. ~/.local/bin/usbinspect -> .venv/bin/usbinspect
#   3. the systemd user service running `usbinspect daemon`
#   4. the Omarchy bar widget, if the Omarchy shell is present
# Re-running is safe. `./install.sh --uninstall` undoes steps 2-4 and keeps the venv,
# your config (~/.config/usbinspect) and the event history (~/.local/state/usbinspect).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${USBINSPECT_VENV:-$REPO/.venv}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
BIN_LINK="$BIN_DIR/usbinspect"
PLUGIN_ID="usb.superposition"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/usb-superposition"
PYTHON="${PYTHON:-python3}"

do_service=1 do_widget=1 extras="tui,daemon" action=install

usage() {
  cat <<EOF
Usage: ./install.sh [options]

  --no-service   don't install the systemd user service
  --no-widget    don't install the Omarchy bar widget
  --dev          install the dev extras too (pytest, ruff, build)
  --uninstall    remove the service, widget and command symlink (keeps .venv,
                 config and history)
  -h, --help     show this help
EOF
}

while (($#)); do
  case "$1" in
    --no-service) do_service=0 ;;
    --no-widget) do_widget=0 ;;
    --dev) extras="tui,daemon,dev" ;;
    --uninstall) action=uninstall ;;
    -h | --help) usage; exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# A symlink at $1 that points at $2 (resolved).
links_to() { [[ -L $1 ]] && [[ "$(readlink -f "$1")" == "$(readlink -f "$2")" ]]; }

have_systemd_user() { have systemctl && systemctl --user show-environment >/dev/null 2>&1; }
have_omarchy_shell() { have omarchy && have omarchy-shell && [[ -d "$(dirname "$PLUGIN_DIR")" ]]; }

# Point $1 at $2, replacing an old symlink but never a real file or directory.
link() {
  local dest=$1 target=$2
  if links_to "$dest" "$target"; then
    return 0
  elif [[ -L $dest ]]; then
    ln -sfn "$target" "$dest"
  elif [[ -e $dest ]]; then
    die "$dest exists and is not a symlink; move it away and re-run"
  else
    ln -s "$target" "$dest"
  fi
  say "linked $dest -> $target"
}

install_venv() {
  if [[ ! -x $VENV/bin/python ]]; then
    have "$PYTHON" || die "$PYTHON not found"
    "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
      die "Python 3.11 or newer is required ($("$PYTHON" --version 2>&1))"
    say "creating $VENV"
    "$PYTHON" -m venv --system-site-packages "$VENV"
  fi
  say "installing usb-superposition[$extras] (editable) into $VENV"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -e "$REPO[$extras]"
}

install_command() {
  mkdir -p "$BIN_DIR"
  link "$BIN_LINK" "$VENV/bin/usbinspect"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; add it to run plain \`usbinspect\`" ;;
  esac
}

install_service() {
  if ! have_systemd_user; then
    warn "no systemd user session; skipping the service (run \`usbinspect daemon\` yourself)"
    return 0
  fi
  # With $BIN_DIR first on PATH the unit's ExecStart is the stable symlink, not the venv path.
  PATH="$BIN_DIR:$PATH" "$BIN_LINK" service install
}

install_widget() {
  if ! have_omarchy_shell; then
    say "Omarchy shell not found; skipping the bar widget"
    return 0
  fi
  link "$PLUGIN_DIR" "$REPO/omarchy"
  if ! omarchy-shell -q shell rescanPlugins >/dev/null 2>&1; then
    warn "the Omarchy shell isn't running; once it is, run: omarchy plugin enable $PLUGIN_ID"
    return 0
  fi
  # The shell is busy for a moment after a rescan and drops calls, hence the retries.
  # `bar put` leaves the widget where it is if it's already on the bar.
  if retry omarchy bar put "$PLUGIN_ID" --before omarchy.bluetooth && retry omarchy plugin enable "$PLUGIN_ID"; then
    say "widget enabled on the bar"
  else
    warn "couldn't reach the Omarchy shell; run: omarchy plugin enable $PLUGIN_ID"
  fi
}

# Run "$@" up to 5 times, a second apart, quietly; succeed if any attempt does.
retry() {
  local i
  for i in 1 2 3 4 5; do
    "$@" >/dev/null 2>&1 && return 0
    ((i < 5)) && sleep "${RETRY_DELAY:-1}"
  done
  return 1
}

uninstall() {
  if [[ -L $PLUGIN_DIR ]] && links_to "$PLUGIN_DIR" "$REPO/omarchy"; then
    if have_omarchy_shell; then
      omarchy plugin disable "$PLUGIN_ID" || true
    fi
    rm "$PLUGIN_DIR"
    say "removed $PLUGIN_DIR"
  fi
  if have_systemd_user && { [[ -x $BIN_LINK ]] || [[ -x $VENV/bin/usbinspect ]]; }; then
    local cmd=$BIN_LINK
    [[ -x $cmd ]] || cmd=$VENV/bin/usbinspect
    "$cmd" service uninstall || true
  fi
  if links_to "$BIN_LINK" "$VENV/bin/usbinspect"; then
    rm "$BIN_LINK"
    say "removed $BIN_LINK"
  fi
  say "kept $VENV, ~/.config/usbinspect and ~/.local/state/usbinspect; delete them by hand if you like"
}

if [[ $action == uninstall ]]; then
  uninstall
  exit 0
fi

install_venv
install_command
((do_service)) && install_service
((do_widget)) && install_widget

say "done. Try: usbinspect   (or: usbinspect tui, usbinspect calibrate)"
