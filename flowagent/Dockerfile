# Flow Agent's backend, deployed from the copy in this repository.
#
# It used to be cloned from GitHub at build time. That made every deploy depend
# on somebody else's repository still being there and on the builder having
# network — so the code lives in `upstream/` now and this only installs it.
# Nothing of it is modified; see upstream/VENDORED.md.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY flowagent/upstream/flow-agent /srv/flow-agent
# Two endpoints Flow Agent does not have, in a module that imports its app
# rather than editing it — see the file's own docstring. Copied beside the
# vendored tree, never into it, so an upstream update overwrites nothing.
COPY flowagent/sarideo_extras.py /srv/flow-agent/sarideo_extras.py

WORKDIR /srv/flow-agent
RUN pip install --no-cache-dir . && pip install --no-cache-dir "uvicorn[standard]"

# Generated files and the media-id map live here. On Railway, mount a volume at
# /data or every redeploy forgets which reference photo was which.
ENV FLOW_OUTPUT_DIR=/data/flow
ENV PYTHONUNBUFFERED=1

# Slower than upstream's defaults, not faster.
#
# Raising these was a mistake: what is behind this is one Google account, and
# Google's own anti-abuse decides whether the traffic looks like a person. Push
# it and every call comes back
#
#     reCAPTCHA evaluation failed (PUBLIC_ERROR_UNUSUAL_ACTIVITY)
#
# which is not a rate limit you wait out — it is a judgement about the pattern.
# Two at a time, six seconds apart, is roughly what somebody working in the page
# would produce. Slower still if it keeps happening.
ENV MAX_CONCURRENT_REQUESTS=2
ENV REQUEST_MIN_INTERVAL=6

# DEFAULT_PROJECT is deliberately NOT set here. Upstream falls back to a project
# id baked into its source — somebody else's — and pictures made against it are
# not made in yours. Set it per deployment to your own; flowagent/README.md says
# where to find it.
#
# It can also be set at runtime, from Sarideo, without a redeploy: that is what
# `sarideo_extras` adds, and it is the answer to a project that has hit a limit.

# 0.0.0.0 rather than the upstream default of 127.0.0.1: a container that only
# listens to itself is a container the platform reports as dead. `$PORT` is what
# Railway assigns; 8001 is what it is called everywhere else.
CMD ["sh", "-c", "mkdir -p \"$FLOW_OUTPUT_DIR\" && exec uvicorn sarideo_extras:app --host 0.0.0.0 --port ${PORT:-8001} --no-access-log"]
