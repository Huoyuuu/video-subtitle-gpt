#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/home/huoyuuu/video-subtitle-gpt
PORT=18004
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt
sudo cp deploy/video-subtitle-gpt.service /etc/systemd/system/video-subtitle-gpt.service
sudo systemctl daemon-reload
sudo systemctl enable --now video-subtitle-gpt
sudo systemctl restart video-subtitle-gpt
sudo systemctl status video-subtitle-gpt --no-pager

