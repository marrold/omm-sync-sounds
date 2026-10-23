#!/usr/bin/env python3
"""split-voices.py VOICES_JSON OUT_DIR: write OUT_DIR/<language>.json for each
language in voices.json, holding the defaults and just that language's entry.

A Docker stage copies only its own file, so editing one language's voice
doesn't invalidate the others' cached sounds (editing the defaults does).
"""
import json
import os
import sys

voices = json.load(open(sys.argv[1]))
os.makedirs(sys.argv[2], exist_ok=True)
for language, settings in voices["languages"].items():
    with open(os.path.join(sys.argv[2], language + ".json"), "w") as f:
        json.dump({"defaults": voices["defaults"], "languages": {language: settings}}, f, indent=2, sort_keys=True)
