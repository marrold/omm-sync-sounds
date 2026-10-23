#!/usr/bin/env python3
"""Generate Asterisk-compatible audio files using Kokoro or Piper TTS.

csv/<set>-<language>.csv  ->  <output>/<language>/<filename>.{wav,alaw,gsm}

The language is the part of the CSV name after the last "-" (en_GB, de, nl_BE),
and is also the name of Asterisk's per-language sounds directory. Several CSVs
for one language share its directory, so a filename may carry a path prefix
(omm-sync/code-intro) to keep a set apart. voices.json says which engine,
voice and settings each language uses.
"""
import argparse
import time
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import wave

import numpy as np

TARGET_RATE = int(os.environ.get("TARGET_RATE", "8000"))
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
VOICES_FILE = os.environ.get("VOICES_FILE", "/app/voices.json")
FALLBACK_LANGUAGE = "en_GB"
CSV_NAME = re.compile(r"^(?P<set>.+)-(?P<language>[a-z]{2,3}(?:_[A-Z]{2})?)$")
MANIFEST_SCHEMA_VERSION = 2


def generator_fingerprint():
    """Identify all generator/parser behaviour that can affect an output file."""
    with open(__file__, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()[:16]


GENERATOR_FINGERPRINT = generator_fingerprint()


class Voice:
    """A language's voice and the settings it is rendered with."""

    def __init__(self, language, voices):
        settings = {**voices["defaults"], **voices["languages"][language]}
        self.name = settings["voice"]
        self.lead_in_ms = int(settings["lead_in_ms"])
        # A set may override some settings (voices.json "sets"): the clock's
        # clips are placed to the sample, so they get no lead-in.
        self.set_overrides = voices.get("sets", {})
        self._model = None

    def lead_in_for(self, set_name):
        return int(self.set_overrides.get(set_name, {}).get("lead_in_ms", self.lead_in_ms))

    def config_hash_for(self, set_name):
        if self.lead_in_for(set_name) == self.lead_in_ms:
            return self.config_hash
        return hashlib.sha256(f"{self.config_hash}:{self.lead_in_for(set_name)}".encode()).hexdigest()[:16]

    def _hash(self, params):
        # Hash of all parameters that affect audio output — changes trigger regeneration.
        params = {**params, "engine": self.engine, "voice": self.name,
                  "lead_in_ms": self.lead_in_ms, "target_rate": TARGET_RATE,
                  "generator": GENERATOR_FINGERPRINT}
        return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]


class PiperVoice(Voice):
    engine = "piper"

    def __init__(self, language, voices):
        super().__init__(language, voices)
        from piper import SynthesisConfig
        settings = {**voices["defaults"], **voices["languages"][language]}
        self.synthesis_config = SynthesisConfig(
            noise_scale=float(settings["noise_scale"]),
            noise_w_scale=float(settings["noise_w_scale"]),
            length_scale=float(settings["length_scale"]),
        )
        self.model_path = os.path.join(MODELS_DIR, self.name + ".onnx")
        self.model_files = [self.model_path]
        self.config_hash = self._hash({
            "noise_scale": self.synthesis_config.noise_scale,
            "noise_w_scale": self.synthesis_config.noise_w_scale,
            "length_scale": self.synthesis_config.length_scale,
        })

    def synthesize(self, text: str) -> bytes:
        if self._model is None:
            from piper import PiperVoice as Piper
            print(f"Loading voice {self.name}...")
            self._model = Piper.load(self.model_path)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self._model.synthesize_wav(text, wf, syn_config=self.synthesis_config)
        return buf.getvalue()


class KokoroVoice(Voice):
    engine = "kokoro"

    def __init__(self, language, voices):
        super().__init__(language, voices)
        settings = {**voices["defaults"], **voices["languages"][language]}
        self.lang = settings["lang"]
        self.speed = float(settings["speed"])
        # A language may bring its own fine-tuned model (German does).
        self.model_files = [
            os.path.join(MODELS_DIR, settings.get("model", "kokoro-v1.0.onnx")),
            os.path.join(MODELS_DIR, settings.get("voices_file", "voices-v1.0.bin")),
        ]
        self.config_hash = self._hash({
            "lang": self.lang, "speed": self.speed,
            "model": os.path.basename(self.model_files[0]),
        })

    def synthesize(self, text: str) -> bytes:
        if self._model is None:
            from kokoro_onnx import Kokoro
            print(f"Loading voice {self.name}...")
            self._model = Kokoro(*self.model_files)
        samples, rate = self._model.create(text, voice=self.name, speed=self.speed, lang=self.lang)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()


ENGINES = {"piper": PiperVoice, "kokoro": KokoroVoice}


def make_voice(language, voices):
    settings = voices["languages"][language]
    return ENGINES[settings["engine"]](language, voices)


def parse_csv_dir(directory):
    """Return [(set, language, [(filename, text), ...]), ...]."""
    groups = []
    for csv_file in sorted(f for f in os.listdir(directory) if f.endswith(".csv")):
        stem = os.path.splitext(csv_file)[0]
        match = CSV_NAME.match(stem)
        if not match:
            print(f"WARNING: {csv_file} is not named <set>-<language>.csv, skipped", file=sys.stderr)
            continue
        entries = {}
        with open(os.path.join(directory, csv_file), newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row or row[0].strip().startswith("#"):
                    continue
                if row[0].strip().lower() == "filename":
                    continue
                if len(row) < 2:
                    continue
                filename = row[0].strip()
                text = row[1].strip()
                if filename and text:
                    entries[filename] = text
        if entries:
            groups.append((match["set"], match["language"], list(entries.items())))
    return groups


def manifest_path(output_dir, set_name):
    return os.path.join(output_dir, f".manifest-{set_name}.json")


def load_manifest(output_dir, set_name):
    try:
        with open(manifest_path(output_dir, set_name)) as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(manifest, dict):
        return None
    entries = manifest.get("entries")
    if (manifest.get("_schema_version") != MANIFEST_SCHEMA_VERSION
            or not isinstance(manifest.get("_config_hash"), str)
            or not isinstance(entries, dict)
            or not all(isinstance(name, str) and isinstance(value, str)
                       for name, value in entries.items())):
        return None
    return manifest


def save_manifest(output_dir, set_name, manifest):
    with open(manifest_path(output_dir, set_name), "w") as f:
        json.dump(manifest, f, indent=2)


def convert(src_wav: bytes, out_path: str, codec_args: list, lead_in_ms: int = 0) -> None:
    command = ["ffmpeg", "-y", "-i", "pipe:0"]
    if lead_in_ms:
        command += ["-af", f"adelay={lead_in_ms}"]
    result = subprocess.run(
        command + [
            "-ar", str(TARGET_RATE), "-ac", "1",
        ] + codec_args + [out_path],
        input=src_wav,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode().strip())


def process_group(set_name, language, entries, args, voice):
    out_dir = os.path.join(args.output, language)
    os.makedirs(out_dir, exist_ok=True)

    cache_root = args.cache or args.output
    cache_dir = os.path.join(cache_root, language)
    manifest = load_manifest(cache_dir, set_name)
    cached_entries = manifest["entries"] if manifest else {}
    config_hash = voice.config_hash_for(set_name)
    lead_in_ms = voice.lead_in_for(set_name)
    config_changed = not manifest or manifest["_config_hash"] != config_hash
    new_manifest = {
        "_schema_version": MANIFEST_SCHEMA_VERSION,
        "_config_hash": config_hash,
        "entries": {},
    }

    print(f"[{language}/{set_name}] {len(entries)} entries ({voice.name})", end="")
    if config_changed and not args.regenerate_all:
        print(" (cache missing or incompatible — regenerating all)", end="")
    print()

    generated = skipped = failed = 0

    for filename, text in entries:
        wav_path = os.path.join(out_dir, filename + ".wav")
        alaw_path = os.path.join(out_dir, filename + ".alaw")
        gsm_path = os.path.join(out_dir, filename + ".gsm")
        cached_paths = [
            os.path.join(cache_dir, filename + ".wav"),
            os.path.join(cache_dir, filename + ".alaw"),
            os.path.join(cache_dir, filename + ".gsm"),
        ]

        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        files_exist = all(os.path.isfile(path) for path in cached_paths)
        up_to_date = files_exist and not config_changed and cached_entries.get(filename) == text_hash

        if not args.regenerate_all and up_to_date:
            if os.path.abspath(cache_dir) != os.path.abspath(out_dir):
                os.makedirs(os.path.join(out_dir, os.path.dirname(filename)), exist_ok=True)
                for source, destination in zip(cached_paths, [wav_path, alaw_path, gsm_path]):
                    shutil.copy2(source, destination)
            new_manifest["entries"][filename] = text_hash
            skipped += 1
            continue

        print(f"  GEN   {filename}: {text}")
        try:
            os.makedirs(os.path.join(out_dir, os.path.dirname(filename)), exist_ok=True)
            src_wav = voice.synthesize(text)
            convert(src_wav, wav_path, ["-acodec", "pcm_s16le"], lead_in_ms)
            convert(src_wav, alaw_path, ["-acodec", "pcm_alaw", "-f", "alaw"], lead_in_ms)
            convert(src_wav, gsm_path, ["-acodec", "libgsm"], lead_in_ms)
            new_manifest["entries"][filename] = text_hash
            generated += 1
        except Exception as exc:
            print(f"  ERROR {filename}: {exc}", file=sys.stderr)
            failed += 1

    # When updating an output directory in place, remove files formerly owned
    # by this set. Docker builds use a separate cache directory and naturally
    # omit deleted entries from their fresh /out directory.
    if os.path.abspath(cache_dir) == os.path.abspath(out_dir):
        current_names = {filename for filename, _ in entries}
        for filename in set(cached_entries) - current_names:
            for extension in (".wav", ".alaw", ".gsm"):
                try:
                    os.remove(os.path.join(out_dir, filename + extension))
                except FileNotFoundError:
                    pass

    save_manifest(out_dir, set_name, new_manifest)
    print(f"  {generated} generated, {skipped} skipped, {failed} failed\n")
    return generated, skipped, failed


def report_missing(groups):
    """Say which sounds a language lacks compared with the fallback language
    (Asterisk plays the fallback's instead)."""
    names = {}
    for set_name, language, entries in groups:
        names.setdefault(language, {}).setdefault(set_name, set()).update(f for f, _ in entries)
    for language, sets in sorted(names.items()):
        if language == FALLBACK_LANGUAGE:
            continue
        for set_name, files in sorted(names.get(FALLBACK_LANGUAGE, {}).items()):
            missing = files - sets.get(set_name, set())
            if missing:
                print(f"  {language}/{set_name}: {len(missing)} of {len(files)} sounds fall back to {FALLBACK_LANGUAGE}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate Asterisk-compatible audio files using Kokoro or Piper TTS"
    )
    parser.add_argument(
        "--input",
        default=os.environ.get("INPUT_DIR", "/app/csv"),
        help="Directory containing CSV files to process",
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("OUTPUT_DIR", "/sounds"),
        help="Root output directory (one subdirectory per language)",
    )
    parser.add_argument(
        "--cache",
        help="Previous output tree to reuse; incompatible caches are ignored",
    )
    parser.add_argument(
        "--voices",
        default=VOICES_FILE,
        help="voices.json: the engine, voice and settings for each language",
    )
    parser.add_argument(
        "--lang",
        action="append",
        help="Only generate this language (repeatable)",
    )
    parser.add_argument(
        "--all",
        dest="regenerate_all",
        action="store_true",
        help="Regenerate all files, even if they already exist",
    )
    args = parser.parse_args()
    start = time.monotonic()

    if not os.path.isdir(args.input):
        print(f"ERROR: input directory not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.voices) as f:
        voices = json.load(f)

    groups = parse_csv_dir(args.input)
    if args.lang:
        groups = [g for g in groups if g[1] in args.lang]
    if not groups:
        print("No entries found in CSV files.", file=sys.stderr)
        sys.exit(1)

    unknown = sorted({lang for _, lang, _ in groups} - set(voices["languages"]))
    if unknown:
        print(f"ERROR: no voice for {', '.join(unknown)} in {args.voices}", file=sys.stderr)
        sys.exit(1)
    for name in sorted({f for lang in {l for _, l, _ in groups}
                        for f in make_voice(lang, voices).model_files}):
        if not os.path.isfile(name):
            print(f"ERROR: model not found: {name}", file=sys.stderr)
            sys.exit(1)

    total_entries = sum(len(e) for _, _, e in groups)
    print(f"Input : {args.input} ({len(groups)} files, {total_entries} entries)")
    print(f"Output: {args.output}")
    print()

    voice_for = {}
    totals = [0, 0, 0]
    for set_name, language, entries in groups:
        voice = voice_for.get(language) or voice_for.setdefault(language, make_voice(language, voices))
        g, s, f = process_group(set_name, language, entries, args, voice)
        totals[0] += g
        totals[1] += s
        totals[2] += f

    report_missing(groups)
    elapsed = time.monotonic() - start
    print(f"Total: {totals[0]} generated, {totals[1]} skipped, {totals[2]} failed — {elapsed:.1f}s")
    if totals[2]:
        sys.exit(1)


if __name__ == "__main__":
    main()
