const content = document.getElementById("content");
const statusLine = document.getElementById("status");
const warning = document.getElementById("warning");

const ERRORS = {
  no_host: "Связь с приложением не настроена. Запустите install_browser_host.py (см. README).",
  not_running: "Приложение Password Vault не запущено.",
  locked: "Хранилище заблокировано. Разблокируйте его в приложении.",
  disabled: "Заполнение в браузере выключено в настройках приложения.",
  not_web_page: "На этой странице нечего заполнять.",
  site_mismatch: "Запись не относится к этому сайту.",
  touch_id_failed: "Touch ID не подтверждён.",
  reauth_required: "Подтвердите вход в приложении Password Vault.",
  not_found: "Запись не найдена — обновите список.",
  origin_changed: "Страница сменилась, попробуйте ещё раз.",
  timeout: "Приложение не ответило вовремя.",
};

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function setStatus(text, isError = false) {
  statusLine.textContent = text || "";
  statusLine.className = isError ? "error" : "";
}

function errorText(reply) {
  return ERRORS[reply.error] || reply.message || "Неизвестная ошибка.";
}

function showMessage(text, withOpenButton) {
  content.replaceChildren();
  const note = document.createElement("div");
  note.className = "muted";
  note.textContent = text;
  content.append(note);
  if (withOpenButton) {
    const button = document.createElement("button");
    button.className = "button";
    button.textContent = "Открыть Password Vault";
    button.addEventListener("click", async () => {
      await send({ cmd: "open_app" });
      window.close();
    });
    content.append(button);
  }
}

async function main() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    document.getElementById("host").textContent = new URL(tab.url).host;
  } catch {
    document.getElementById("host").textContent = "";
  }
  const reply = await send({ cmd: "list", tabId: tab.id });
  if (!reply.ok) {
    showMessage(errorText(reply), ["not_running", "locked"].includes(reply.error));
    return;
  }
  if (!reply.entries.length) {
    showMessage("Для этого сайта нет сохранённых логинов. Добавьте запись с этим адресом в приложении.");
    return;
  }
  warning.hidden = !reply.insecure;
  let armed = !reply.insecure;
  content.replaceChildren();
  for (const entry of reply.entries) {
    const button = document.createElement("button");
    button.className = "entry";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = (entry.title || "?").slice(0, 1).toUpperCase();
    const text = document.createElement("span");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = entry.title;
    const user = document.createElement("div");
    user.className = "user";
    user.textContent = entry.username || "без логина";
    text.append(name, user);
    button.append(badge, text);
    button.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        setStatus("Нажмите ещё раз, чтобы заполнить на http-сайте.");
        return;
      }
      setStatus("Заполняю… (может понадобиться Touch ID)");
      const result = await send({ cmd: "fill", tabId: tab.id, id: entry.id });
      if (result.ok) {
        window.close();
      } else {
        setStatus(result.error ? errorText(result) : "Поля для входа на странице не найдены.", true);
      }
    });
    content.append(button);
  }
}

main().catch((err) => showMessage(String(err)));
