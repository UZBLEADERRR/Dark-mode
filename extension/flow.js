// The extension's half of the Flow driver: messages in, pictures out.
//
// Everything about the page itself is in `flow-dom.js`, which the manifest loads
// alongside this file. That split is the point — the agent in `agent/` drives
// Flow with the same code by evaluating that file in the page, so a change
// Google makes is fixed once and both of them get it.

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg?.kind === "sarideo:ping") {
    // "This page is loaded and I am listening." The background script waits for
    // this before sending a prompt, because a tab it has just opened is not yet
    // a page that can answer.
    respond({ ok: true });
    return false;
  }
  if (msg?.kind === "sarideo:make") {
    window.sarideoFlow.make(msg.prompt)
      .then((dataUrl) => respond({ dataUrl }))
      .catch((exc) => respond({ error: exc.message }));
    return true;
  }
  if (msg?.kind === "sarideo:count") {
    respond({ count: window.sarideoFlow.count() });
    return false;
  }
  if (msg?.kind === "sarideo:harvest") {
    // A slice at a time: the answer carries the picture bytes, and the whole
    // page's worth would not fit in one reply.
    window.sarideoFlow.harvest(msg.from || 0, msg.count || 4)
      .then((pictures) => respond({ pictures }))
      .catch((exc) => respond({ error: exc.message }));
    return true;
  }
  if (msg?.kind === "sarideo:probe") {
    // What this page looks like to the extension, in the terms it cares about.
    // Reported rather than guessed at, so a broken selector is a fact instead of
    // a theory.
    respond(window.sarideoFlow.probe());
    return true;
  }
  return false;
});
