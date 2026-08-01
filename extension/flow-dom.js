// The only file that knows what Google Flow's page looks like.
//
// Everything else in this extension is stable: a queue, a fetch, a PNG. This
// part is not, and cannot be — it drives someone else's web app by walking its
// DOM, and that DOM changes whenever they ship. So it is kept small, kept in one
// file, and written to be repaired rather than understood: every selector is in
// the block below, each one is a *list* of candidates tried in order, and the
// list can be replaced from the extension's options page without touching code.
//
// If Flow changes and this stops working, open the options page, press "Flow
// sahifasini tekshirish", and it will report what it can and cannot find on the
// page as it is today. That is the whole debugging story.

const SELECTORS = {
  // Where the prompt is typed.
  prompt: [
    'textarea[placeholder*="prompt" i]',
    'textarea[aria-label*="prompt" i]',
    'div[contenteditable="true"][aria-label*="prompt" i]',
    'div[contenteditable="true"]',
    "textarea",
  ],
  // What starts the generation.
  submit: [
    'button[aria-label*="generate" i]',
    'button[aria-label*="create" i]',
    'button[type="submit"]',
  ],
  // Where finished pictures land. Anything matching that is *new* since the
  // prompt was sent, and big enough not to be an icon, counts as the answer.
  result: ["img"],
};

// A picture, not a spinner or an avatar.
const MIN_SIDE = 256;
// How long to wait for Flow to finish one image before giving up on it. Flow is
// slow by nature and this is not a reason to fail a scene, so the caller treats
// a timeout as retryable.
const WAIT_MS = 5 * 60 * 1000;

function pick(list) {
  for (const selector of list) {
    const found = document.querySelector(selector);
    if (found) return found;
  }
  return null;
}

function seen() {
  const out = new Set();
  for (const selector of SELECTORS.result) {
    document.querySelectorAll(selector).forEach((el) => {
      if (el.src) out.add(el.src);
    });
  }
  return out;
}

function big(img) {
  const w = img.naturalWidth || img.width || 0;
  const h = img.naturalHeight || img.height || 0;
  return w >= MIN_SIDE && h >= MIN_SIDE;
}

/** Put text into whatever kind of input Flow is using this month. */
function type(field, text) {
  field.focus();
  if (field.tagName === "TEXTAREA" || field.tagName === "INPUT") {
    // The native setter, not `.value =`. React tracks the last value it wrote
    // and ignores an assignment it did not make — the field would look right on
    // screen and submit empty, which is the worst possible failure here.
    const proto = field.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(field, text);
  } else {
    field.textContent = text;
  }
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

function submit(field) {
  const button = pick(SELECTORS.submit);
  if (button && !button.disabled) {
    button.click();
    return true;
  }
  // No button found, or it is disabled while the field settles: Enter is what a
  // person would press, and Flow accepts it.
  field.dispatchEvent(new KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true,
  }));
  return true;
}

/** Resolve with the src of the first picture that appears and was not there. */
function waitForNew(before) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => {
      observer.disconnect();
      reject(new Error("Flow belgilangan vaqtda rasm bermadi"));
    }, WAIT_MS);

    const check = () => {
      for (const selector of SELECTORS.result) {
        for (const img of document.querySelectorAll(selector)) {
          if (!img.src || before.has(img.src) || !big(img)) continue;
          // A picture that is on the page but not decoded yet has no bytes to
          // take, so wait for it rather than grabbing a blank frame.
          const done = () => {
            clearTimeout(deadline);
            observer.disconnect();
            resolve(img.src);
          };
          if (img.complete && img.naturalWidth) done();
          else img.addEventListener("load", done, { once: true });
          return true;
        }
      }
      return false;
    };

    const observer = new MutationObserver(check);
    observer.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ["src"],
    });
    check();
  });
}

/** A blob: URL belongs to this page and only this page can read it. */
async function toDataUrl(src) {
  const resp = await fetch(src);
  const blob = await resp.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Rasmni o'qib bo'lmadi"));
    reader.readAsDataURL(blob);
  });
}

async function make(prompt) {
  const field = pick(SELECTORS.prompt);
  if (!field) throw new Error("Flow sahifasida prompt maydoni topilmadi");

  const before = seen();
  type(field, prompt);
  // A moment for the page to notice the field changed and enable its button.
  await new Promise((r) => setTimeout(r, 400));
  submit(field);

  const src = await waitForNew(before);
  return toDataUrl(src);
}

// Exposed on `window` rather than kept to this file, because two different
// programs drive Flow with it: the extension's content script, which is loaded
// alongside this file and shares its scope, and the standalone agent in
// `agent/`, which reads this file and evaluates it in the page. One copy of the
// fragile part, or they drift and only one of them gets fixed.
window.sarideoFlow = {
  make,
  probe: () => ({
    url: location.href,
    prompt: pick(SELECTORS.prompt) ? `${pick(SELECTORS.prompt).tagName.toLowerCase()} topildi` : "topilmadi",
    submit: pick(SELECTORS.submit) ? "topildi" : "topilmadi (Enter ishlatiladi)",
    pictures: [...seen()].length,
  }),
};
