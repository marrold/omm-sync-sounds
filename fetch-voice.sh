#!/bin/sh
# fetch-voice.sh LANGUAGE: download the Piper voice voices.json names for it
# into /models.
set -e
voice=$(python3 -c "import json, sys; print(json.load(open('voices.json'))['languages'][sys.argv[1]]['voice'])" "$1")
exec python3 -m piper.download_voices --download-dir /models "$voice"
