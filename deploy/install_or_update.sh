#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/home/huoyuuu/video-subtitle-gpt
REPO_URL=${1:-"https://github.com/huoyuuu/video-subtitle-gpt.git"}
if [ ! -d "$APP_DIR/.git" ]; then
  sudo mkdir -p "$APP_DIR"
  sudo chown -R "$USER:$USER" "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR" && git pull --ff-only
fi
cd "$APP_DIR"
[ -f .env ] || cp .env.example .env
bash scripts/deploy.sh

