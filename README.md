# omm-sync-sounds

Spoken prompts for the omm-sync media server
([omm-sync-utility-server](../fedect-asterisk-utility)) and anything else that
wants them, generated with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx)
(Piper for Dutch, which Kokoro has no voice for). Nothing generated is kept in
git: CI builds a data-only image, `ghcr.io/marrold/omm-sync-sounds`, that
holds just the audio:

```
/<language>/<sound>.{wav,alaw,gsm}     en_GB, de, fr, nl, nl_BE, es, it
/en_GB/beep.wav                         sounds that aren't text (static/)
```

Use it from another image:

```dockerfile
COPY --from=ghcr.io/marrold/omm-sync-sounds:latest /en_GB /sounds-src/en_GB
```

Pin a tag or digest instead of `latest` so a voice or translation change
doesn't reach a service until you choose.

## Sources

`csv/<set>-<language>.csv` is the text (`filename,text`). The language is the
part after the last `-` (`en_GB`, `de`, `nl_BE`) and is also the output
directory; a filename may carry a path (`omm-sync/ring`). Several sets share a
language's directory, so give your own set a prefix (`clock-en_GB.csv`) and
keep its filenames under their own directory.

The `clock` set (`clock-<language>.csv`) is for
[asterisk-ws-clock](../asterisk-ws-clock): the pieces its synthetic voices string together
into "at the third stroke, the time will be…", with no lead-in silence
(`sets` in `voices.json`) because the clock places each to the sample.

`voices.json` says which engine, voice and settings each language uses
(`kokoro` or `piper`; a Kokoro voice may name its own `model` and
`voices_file`, as German does for the fine-tuned Martin model). A language
that lacks a sound plays the en_GB one in Asterisk, so en_GB is the fallback
language.

## Building

Each language is its own Docker stage, cached and built in parallel. CI also
uses the last `latest` image as an incremental source: versioned manifests let
`generate.py` copy unchanged clips and synthesize only entries whose text or
effective settings changed. Deleted entries disappear because every build
writes a fresh output tree. A missing, corrupt or incompatible manifest is a
cache miss, so manifest, CSV-parser or generator changes safely regenerate the
affected set. To listen to them with a clean local build:

```bash
docker build --target sounds --output output .
```

To add a language: add its engine and voice to `voices.json`, add its csvs, a
`sound-<language>` stage in the `Dockerfile` (`FROM kokoro` or `FROM piper`,
to match the engine), a cache copy from `previous-sounds`, and a `COPY --from=`
for it in the last stage.

## Licence

MIT (see `LICENSE`). The Kokoro and Piper voices have their own licences; the
German voice is [Godelaune/Kokoro-82M-ONNX-German-Martin](https://huggingface.co/Godelaune/Kokoro-82M-ONNX-German-Martin)
(Apache-2.0).
