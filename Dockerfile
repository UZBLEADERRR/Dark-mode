FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# ffmpeg + libass (subtitle burn-in) + the fonts the captions are set in.
# DejaVu and Noto core cover Latin, Cyrillic, Greek, Arabic and Devanagari but
# have no Hangul, and libass does not fail on a missing glyph — it draws nothing,
# so a Korean video would come out with blank subtitles. Nanum is 10 MB and is
# what Korean text is actually set in; the whole Noto CJK family is six times the
# size and only worth it once Japanese or Chinese are on the list too.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-noto-core \
        fonts-nanum \
        fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Flow Agent's browser extension, handed out from the library already pointed at
# this deployment. It is data the app serves, not code it imports, which is
# exactly why it was missed: `COPY app` alone left the panel reporting the files
# as missing on every deploy while they sat there in the repository.
COPY flowagent/upstream/flow-extension ./flowagent/upstream/flow-extension

RUN mkdir -p /data

EXPOSE 8000

# ${PORT:-8000} matters: platforms that inject PORT are honoured, and the
# container still starts when nothing injects it. Do not override this with a
# start command that references $PORT without a fallback — an unset PORT makes
# uvicorn exit with "Option '--port' requires an argument", which the platform
# then reports as a hung healthcheck rather than a crash.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
