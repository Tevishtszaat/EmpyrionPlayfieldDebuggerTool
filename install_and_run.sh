#!/usr/bin/env bash
echo "========================================================"
echo "  Empyrion Playfield Studio - Linux Setup & Launch"
echo "========================================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed!"
    echo "Please install python3 and python3-pip using your package manager."
    exit 1
fi

echo "[*] Checking requirements..."
python3 -m pip install -r requirements.txt --quiet

echo "[*] Launching Empyrion Playfield Studio..."
python3 main.py
