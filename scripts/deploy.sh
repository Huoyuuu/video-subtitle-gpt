#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/home/huoyuuu/video-subtitle-gpt
PORT=18004

install_deno_if_missing() {
  if command -v deno >/dev/null 2>&1; then
    deno --version | head -n 1 || true
    return
  fi
  local arch target tmp
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) target="deno-x86_64-unknown-linux-gnu.zip" ;;
    aarch64|arm64) target="deno-aarch64-unknown-linux-gnu.zip" ;;
    *) echo "Skip Deno install: unsupported arch $arch"; return ;;
  esac
  tmp="$(mktemp -d)"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/${target}" -o "$tmp/deno.zip"
  else
    wget -qO "$tmp/deno.zip" "https://github.com/denoland/deno/releases/latest/download/${target}"
  fi
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$tmp/deno.zip" "$tmp"
  sudo install -m 0755 "$tmp/deno" /usr/local/bin/deno
  rm -rf "$tmp"
  /usr/local/bin/deno --version | head -n 1 || true
}

cd "$APP_DIR"
install_deno_if_missing
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt
sudo cp deploy/video-subtitle-gpt.service /etc/systemd/system/video-subtitle-gpt.service
sudo systemctl daemon-reload
sudo systemctl enable --now video-subtitle-gpt
sudo systemctl restart video-subtitle-gpt
sudo systemctl status video-subtitle-gpt --no-pager
