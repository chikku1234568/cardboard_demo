#!/usr/bin/env bash
# Regenerate everything and validate. Deterministic (seeded).
#   1. media      (ffmpeg; skipped if files exist unless --force-media)
#   2. world      (cast, brand, sessions, chat, all VCS projects incl. the flagship, ground truth)
#   3. derive     (memory events/decisions/splits DERIVED from op logs + chat)
#   4. validate
set -euo pipefail
cd "$(dirname "$0")/../generators"
if [[ "${1:-}" == "--force-media" ]]; then python3 gen_media.py --force; else python3 gen_media.py; fi
python3 gen_world.py
python3 derive_events.py
python3 validate.py
