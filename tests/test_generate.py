import argparse
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import generate


class FakeVoice:
    name = "test-voice"

    def __init__(self):
        self.synthesized = []

    def config_hash_for(self, set_name):
        return "config-for-" + set_name

    def lead_in_for(self, set_name):
        return 0

    def synthesize(self, text):
        self.synthesized.append(text)
        return text.encode()


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def write_clip(root, language, name, contents=b"cached"):
    for extension in (".wav", ".alaw", ".gsm"):
        path = os.path.join(root, language, name + extension)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as output:
            output.write(contents)


def fake_convert(source, out_path, codec_args, lead_in_ms=0):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as output:
        output.write(b"generated:" + source)


class IncrementalGenerationTests(unittest.TestCase):
    def test_reuses_unchanged_clips_and_omits_deleted_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = os.path.join(directory, "cache")
            output = os.path.join(directory, "output")
            language = "en_GB"
            set_name = "prompts"
            write_clip(cache, language, "same")
            write_clip(cache, language, "changed")
            write_clip(cache, language, "deleted")
            manifest = {
                "_schema_version": generate.MANIFEST_SCHEMA_VERSION,
                "_config_hash": "config-for-prompts",
                "entries": {
                    "same": text_hash("same text"),
                    "changed": text_hash("old text"),
                    "deleted": text_hash("deleted text"),
                },
            }
            os.makedirs(os.path.join(cache, language), exist_ok=True)
            with open(generate.manifest_path(os.path.join(cache, language), set_name), "w") as file:
                json.dump(manifest, file)

            voice = FakeVoice()
            args = argparse.Namespace(output=output, cache=cache, regenerate_all=False)
            entries = [("same", "same text"), ("changed", "new text"), ("new", "new clip")]
            with mock.patch.object(generate, "convert", side_effect=fake_convert):
                result = generate.process_group(set_name, language, entries, args, voice)

            self.assertEqual(result, (2, 1, 0))
            self.assertEqual(voice.synthesized, ["new text", "new clip"])
            self.assertTrue(os.path.isfile(os.path.join(output, language, "same.wav")))
            self.assertFalse(os.path.exists(os.path.join(output, language, "deleted.wav")))
            saved = generate.load_manifest(os.path.join(output, language), set_name)
            self.assertEqual(set(saved["entries"]), {"same", "changed", "new"})

    def test_incompatible_manifest_regenerates_instead_of_reusing(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = os.path.join(directory, "cache")
            output = os.path.join(directory, "output")
            language = "en_GB"
            set_name = "prompts"
            write_clip(cache, language, "hello")
            os.makedirs(os.path.join(cache, language), exist_ok=True)
            with open(generate.manifest_path(os.path.join(cache, language), set_name), "w") as file:
                json.dump({"_schema_version": 1, "hello": text_hash("Hello")}, file)

            voice = FakeVoice()
            args = argparse.Namespace(output=output, cache=cache, regenerate_all=False)
            with mock.patch.object(generate, "convert", side_effect=fake_convert):
                result = generate.process_group(
                    set_name, language, [("hello", "Hello")], args, voice
                )

            self.assertEqual(result, (1, 0, 0))
            self.assertEqual(voice.synthesized, ["Hello"])

    def test_in_place_generation_removes_entries_deleted_from_csv(self):
        with tempfile.TemporaryDirectory() as output:
            language = "en_GB"
            set_name = "prompts"
            write_clip(output, language, "keep")
            write_clip(output, language, "delete")
            manifest = {
                "_schema_version": generate.MANIFEST_SCHEMA_VERSION,
                "_config_hash": "config-for-prompts",
                "entries": {
                    "keep": text_hash("Keep"),
                    "delete": text_hash("Delete"),
                },
            }
            with open(generate.manifest_path(os.path.join(output, language), set_name), "w") as file:
                json.dump(manifest, file)

            voice = FakeVoice()
            args = argparse.Namespace(output=output, cache=None, regenerate_all=False)
            with mock.patch.object(generate, "convert", side_effect=fake_convert):
                result = generate.process_group(set_name, language, [("keep", "Keep")], args, voice)

            self.assertEqual(result, (0, 1, 0))
            self.assertFalse(os.path.exists(os.path.join(output, language, "delete.wav")))


if __name__ == "__main__":
    unittest.main()
