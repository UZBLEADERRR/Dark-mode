# The Flow agent, as its own service.
#
# Deliberately not part of the Sarideo image. Chromium is a few hundred megabytes
# and wants memory that the render container needs for ffmpeg — putting them
# together is how a small box starts killing renders. This runs beside it and
# talks over HTTP, so it can live on a different service, a different plan, or a
# PC at home.

# Playwright's own image: Chromium and its system libraries are already in it,
# which is the whole reason to use it. The tag and `agent/requirements.txt` name
# the same version on purpose — see the note there.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The page-driving code is shared with the extension and read at runtime, so the
# agent needs the sibling directory, not just its own.
COPY extension/flow-dom.js /extension/flow-dom.js
COPY agent/sarideo_agent.py ./

# The signed-in browser profile. Mount a volume here — without one, every
# restart is a fresh browser and a fresh sign-in.
ENV SARIDEO_PROFILE=/profile
VOLUME ["/profile"]

# `login` first, once, to sign in; then `run`. Overridden per deployment.
CMD ["python", "sarideo_agent.py", "run"]
