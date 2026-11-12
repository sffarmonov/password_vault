// Password Vault extension: talks to the desktop app through Native Messaging.
// Passwords are never stored by the extension; they pass through only to be
// typed into the page the user explicitly asked to fill.

const HOST = "com.passwordvault.bridge";

function native(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendNativeMessage(HOST, message, (reply) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: "no_host", message: chrome.runtime.lastError.message });
      } else {
        resolve(reply || { ok: false, error: "empty_reply" });
      }
    });
  });
}

function isWebPage(url) {
  return /^https?:\/\//i.test(url || "");
}

// Runs inside the page. Must be self-contained (it is serialized by Chrome).
function pvFill(expectedOrigin, username, password) {
  if (location.origin !== expectedOrigin) {
    return { ok: false, error: "origin_changed" };
  }
  const visible = (el) =>
    !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
    getComputedStyle(el).visibility !== "hidden";
  const usable = (el) => visible(el) && !el.disabled && !el.readOnly;
  const setValue = (el, value) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const passwordField = [...document.querySelectorAll('input[type="password"]')].find(usable);
  const textFields = [...document.querySelectorAll("input")].filter(
    (el) => usable(el) && ["text", "email", "tel"].includes(el.type)
  );
  let userField = null;
  if (passwordField) {
    // The closest text field before the password field, in the same form if there is one.
    for (const el of textFields) {
      if (passwordField.form && el.form !== passwordField.form) continue;
      if (el.compareDocumentPosition(passwordField) & Node.DOCUMENT_POSITION_FOLLOWING) userField = el;
    }
  } else {
    // Two-step logins (e.g. e-mail first, password on the next screen).
    const hint = /user|login|email|e-mail|phone|account|identifier|логин|почт/i;
    userField =
      textFields.find((el) => hint.test([el.name, el.id, el.autocomplete, el.placeholder].join(" "))) ||
      textFields[0] || null;
  }
  if (userField && username) setValue(userField, username);
  if (passwordField && password) setValue(passwordField, password);
  return { ok: !!(userField || passwordField), user: !!userField, password: !!passwordField };
}

async function listFor(tab) {
  if (!tab || !isWebPage(tab.url)) return { ok: false, error: "not_web_page" };
  return native({ cmd: "list", url: tab.url });
}

async function fill(tabId, entryId) {
  const tab = await chrome.tabs.get(tabId);
  if (!isWebPage(tab.url)) return { ok: false, error: "not_web_page" };
  const creds = await native({ cmd: "credentials", id: entryId, url: tab.url });
  if (!creds.ok) return creds;
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: pvFill,
    args: [new URL(tab.url).origin, creds.username || "", creds.password || ""],
  });
  return result && result.result ? result.result : { ok: false, error: "inject_failed" };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message.cmd) {
      case "list":
        return listFor(await chrome.tabs.get(message.tabId));
      case "fill":
        return fill(message.tabId, message.id);
      case "open_app":
        return native({ cmd: "open_app" });
      default:
        return { ok: false, error: "unknown_command" };
    }
  })().then(sendResponse, (err) => sendResponse({ ok: false, error: "exception", message: String(err) }));
  return true; // keep the channel open for the async reply
});

// Keyboard shortcut: fill right away if exactly one login matches, otherwise show the popup.
chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== "fill-login" || !tab) return;
  const list = await listFor(tab);
  if (list.ok && list.entries.length === 1 && !list.insecure) {
    await fill(tab.id, list.entries[0].id);
  } else if (chrome.action.openPopup) {
    chrome.action.openPopup().catch(() => {});
  }
});
