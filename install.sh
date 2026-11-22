#!/usr/bin/env bash
# SpikeClip installer: venv + pip install + tool checks. Free, no accounts.
set -euo pipefail

cd "$(dirname "$0")"

echo "== SpikeClip install =="
command -v python3 >/dev/null || { echo "ERROR: python3 not found (need 3.10+)."; exit 1; }
command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found on PATH — install ffmpeg 8.x first."; exit 1; }
echo "ffmpeg: $(ffmpeg -version | head -1)"

if [ ! -d .venv ]; then
    echo "creating .venv ..."
    python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
echo "installing spikeclip (editable) ..."
./.venv/bin/pip install --quiet -e .

echo "checking tools ..."
./.venv/bin/python -c "import streamlink, faster_whisper, websocket, yaml; print('streamlink + faster-whisper + websocket-client + yaml: OK')"
./.venv/bin/spikeclip --version

echo
echo "Done. Next steps:"
echo "  source .venv/bin/activate"
echo "  spikeclip watch <channel> --check-only     # is the channel live?"
echo "  spikeclip watch <channel>                  # record + auto-clip spikes"
echo "  spikeclip spikes tests/fixtures/chat.json --list-only"
echo
echo "VOD mode also needs TwitchDownloaderCLI (separate download):"
echo "  https://github.com/lay295/TwitchDownloader"
