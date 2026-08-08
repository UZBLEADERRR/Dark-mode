"""Flow Agent, plus the two things Sarideo needs it to do.

Flow Agent draws into one Google Flow project, named by the `DEFAULT_PROJECT`
environment variable. That is fine until the project hits a limit, and then it
is not: changing an environment variable means editing it on the hosting
platform and waiting for a redeploy, which is a bad answer to "this project is
full, use the next one" — especially from a phone.

Two endpoints fix that, and neither edits a line of Flow Agent:

    GET  /sarideo/project   which project it is drawing into
    POST /sarideo/project   draw into this one from now on, and open it

`os.environ` is the right place to put it because that is exactly where Flow
Agent reads it from — `routes/generation.py` calls `os.environ.get` inside the
request handler, on every request, so a value set here applies to the very next
picture with nothing restarted.

Setting it also tells the browser to open that project. Google decides which
project a request belongs to partly from what the tab is showing, so pointing
the backend at a project the browser is not looking at is how you get pictures
filed somewhere you did not expect.

Run this instead of `flow_server.api:app`. Their app object is imported, not
copied, so an upstream update needs nothing here.
"""

from __future__ import annotations

import os
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from flow_server import state
from flow_server.api import app  # noqa: F401 - theirs, unmodified

# A Flow project id is a UUID. Checked because this value is pasted by hand from
# a browser address bar, and a wrong one fails as a picture that never arrives
# rather than as an error anybody can read.
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                  r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

PROJECT_URL = "https://labs.google/fx/tools/flow/project/{}"


class Project(BaseModel):
    project_id: str = Field(min_length=1, max_length=80)
    # Whether to also bring the browser to it. On by default: a backend pointed
    # at one project while the tab shows another is the confusing half-state
    # this endpoint exists to avoid.
    open_tab: bool = True


@app.get("/sarideo/project")
async def which_project() -> dict:
    """Which project pictures are being filed into, and whether a browser is on."""
    bridge = state.get_bridge()
    return {
        "project_id": os.environ.get("DEFAULT_PROJECT", ""),
        "url": PROJECT_URL.format(os.environ.get("DEFAULT_PROJECT", ""))
        if os.environ.get("DEFAULT_PROJECT") else "",
        # `_clients` is theirs and private, hence the guarded read: this is a
        # convenience on a status endpoint, not something to fail a request over.
        "browsers": len(getattr(bridge, "_clients", {}) or {}) if bridge else 0,
    }


@app.post("/sarideo/project", dependencies=[Depends(state.verify_api_key)])
async def use_project(body: Project) -> dict:
    """Draw into this project from now on, and bring the browser to it."""
    wanted = body.project_id.strip()
    if not UUID.match(wanted):
        raise HTTPException(
            status_code=400,
            detail="Loyiha ID noto'g'ri. Flow manzilidagi "
                   "…/project/<ID> qismini to'liq nusxalang.")

    os.environ["DEFAULT_PROJECT"] = wanted

    opened = False
    if body.open_tab:
        bridge = state.get_bridge()
        if bridge is not None:
            # Broadcast, not addressed: every browser helping this backend has to
            # be looking at the same project, or pictures land in whichever one
            # each tab happened to be showing.
            await bridge.send_message({"type": "open_project", "project_id": wanted})
            opened = True

    return {"project_id": wanted, "url": PROJECT_URL.format(wanted), "opened": opened}
