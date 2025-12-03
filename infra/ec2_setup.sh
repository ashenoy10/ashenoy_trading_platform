#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3 python3-venv python3-pip docker.io git

sudo systemctl enable docker
sudo systemctl start docker

cd /opt || exit 1
sudo git clone https://github.com/your-username/quantbot.git || true
cd quantbot || exit 1

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Add your .env file, then run: docker build -t quantbot ."
