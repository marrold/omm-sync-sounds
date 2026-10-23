# The sounds are generated here, from csv/, with Kokoro (or Piper for the
# languages Kokoro has no voice for): nothing generated is kept in git. The
# result is a data-only image (the last stage), /<language>/<sound>.{wav,alaw,gsm},
# for other images to `COPY --from=` (see the README).
# There is a stage per language, so a change to one language's csv only
# invalidates that language (and the stages build in parallel). CI seeds each
# stage from the last published image; generate.py validates its manifests and
# copies unchanged clips, then generates only changed clips into fresh output.
# Without SOUND_CACHE_IMAGE (normal local builds), the empty-cache stage makes
# this a clean build.
#
# To add a language: add its voice to voices.json and a stage like the
# ones below, FROM kokoro or FROM piper to match its engine (and a `COPY
# --from=` for it under the last stage).
ARG SOUND_CACHE_IMAGE=empty-sound-cache

FROM python:3.11-slim AS voices
COPY voices.json split-voices.py /src/
RUN python3 /src/split-voices.py /src/voices.json /voices

FROM python:3.11-slim AS tts-base

RUN apt-get update && apt-get install -y \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
WORKDIR /app

FROM tts-base AS piper
RUN pip install --no-cache-dir piper-tts numpy

FROM tts-base AS kokoro
RUN pip install --no-cache-dir kokoro-onnx
ADD https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx /models/
ADD https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin /models/
# German is a fine-tune (Godelaune/Kokoro-82M-ONNX-German-Martin, Apache-2.0).
ADD https://huggingface.co/Godelaune/Kokoro-82M-ONNX-German-Martin/resolve/main/kokoro-martin.onnx /models/
ADD https://huggingface.co/Godelaune/Kokoro-82M-ONNX-German-Martin/resolve/main/voices-martin.npz /models/

# A local build has no prior sounds and uses this empty directory layout. It is
# generated from voices.json so a newly-added language also works when the last
# published image predates it.
FROM voices AS empty-sound-cache-layout
RUN python3 -c 'import json, os; voices=json.load(open("/src/voices.json")); [os.makedirs("/empty/" + language) for language in voices["languages"]]'

FROM scratch AS empty-sound-cache
COPY --from=empty-sound-cache-layout /empty/ /

# CI overrides SOUND_CACHE_IMAGE with the last published data-only image when
# one exists. Merge it over the empty layout so cache loss and new languages
# both fall back to a clean build.
FROM ${SOUND_CACHE_IMAGE} AS previous-sounds-data
FROM empty-sound-cache AS previous-sounds
COPY --from=previous-sounds-data / /

FROM kokoro AS sound-en_gb
COPY --from=previous-sounds /en_GB/ /cache/en_GB/
COPY --from=voices /voices/en_GB.json voices.json
COPY generate.py .
COPY csv/*-en_GB.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache
# Sounds that aren't generated from text; en_GB is the fallback language.
COPY static/ /out/en_GB/

FROM kokoro AS sound-de
COPY --from=previous-sounds /de/ /cache/de/
COPY --from=voices /voices/de.json voices.json
COPY generate.py .
COPY csv/*-de.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

FROM kokoro AS sound-fr
COPY --from=previous-sounds /fr/ /cache/fr/
COPY --from=voices /voices/fr.json voices.json
COPY generate.py .
COPY csv/*-fr.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

FROM piper AS sound-nl
COPY --from=previous-sounds /nl/ /cache/nl/
COPY --from=voices /voices/nl.json voices.json
COPY fetch-voice.sh ./
RUN sh fetch-voice.sh nl
COPY generate.py .
COPY csv/*-nl.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

FROM piper AS sound-nl_be
COPY --from=previous-sounds /nl_BE/ /cache/nl_BE/
COPY --from=voices /voices/nl_BE.json voices.json
COPY fetch-voice.sh ./
RUN sh fetch-voice.sh nl_BE
COPY generate.py .
COPY csv/*-nl_BE.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

FROM kokoro AS sound-es
COPY --from=previous-sounds /es/ /cache/es/
COPY --from=voices /voices/es.json voices.json
COPY generate.py .
COPY csv/*-es.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

FROM kokoro AS sound-it
COPY --from=previous-sounds /it/ /cache/it/
COPY --from=voices /voices/it.json voices.json
COPY generate.py .
COPY csv/*-it.csv csv/
RUN python3 generate.py --input csv --output /out --cache /cache

# The published image: only the sounds. `docker build --output output .`
# writes them to ./output instead, to listen to them.
FROM scratch AS sounds
COPY --from=sound-en_gb /out/ /
COPY --from=sound-de /out/ /
COPY --from=sound-fr /out/ /
COPY --from=sound-nl /out/ /
COPY --from=sound-nl_be /out/ /
COPY --from=sound-es /out/ /
COPY --from=sound-it /out/ /
