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

WORKDIR /srv/flow-agent
RUN pip install --no-cache-dir . && pip install --no-cache-dir "uvicorn[standard]"

# Generated files and the media-id map live here. On Railway, mount a volume at
# /data or every redeploy forgets which reference photo was which.
ENV FLOW_OUTPUT_DIR=/data/flow
ENV PYTHONUNBUFFERED=1

# Its own defaults are meant for one person at a keyboard. Sarideo asks for three
# scenes at once, so the ceiling is raised and the spacing between calls dropped —
# still paced, because the account behind this is a subscription and not an API
# plan. Both are plain variables: raise or lower them from Railway.
ENV MAX_CONCURRENT_REQUESTS=5
ENV REQUEST_MIN_INTERVAL=2

# DEFAULT_PROJECT is deliberately NOT set here. Upstream falls back to a project
# id baked into its source — somebody else's — and pictures made against it are
# not made in yours. Set it per deployment to your own; flowagent/README.md says
# where to find it.

# 0.0.0.0 rather than the upstream default of 127.0.0.1: a container that only
# listens to itself is a container the platform reports as dead. `$PORT` is what
# Railway assigns; 8001 is what it is called everywhere else.
CMD ["sh", "-c", "mkdir -p \"$FLOW_OUTPUT_DIR\" && exec uvicorn flow_server.api:app --host 0.0.0.0 --port ${PORT:-8001} --no-access-log"]
