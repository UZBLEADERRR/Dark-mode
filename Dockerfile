FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# ffmpeg + libass (subtitle burn-in) + fonts for Latin/Cyrillic captions
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-noto-core \
        fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN mkdir -p /data

EXPOSE 8000

# ${PORT:-8000} matters: platforms that inject PORT are honoured, and the
# container still starts when nothing injects it. Do not override this with a
# start command that references $PORT without a fallback — an unset PORT makes
# uvicorn exit with "Option '--port' requires an argument", which the platform
# then reports as a hung healthcheck rather than a crash.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
