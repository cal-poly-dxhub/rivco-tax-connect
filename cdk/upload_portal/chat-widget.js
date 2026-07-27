/**
 * Riverside County chat widget — vanilla JS, no build step.
 *
 * Reads window.WS_ENDPOINT (set by CDK config.js inject), opens a WebSocket,
 * renders a fixed-bottom-right chat panel, and streams Claude tokens into the
 * latest assistant bubble. Handoff events render a reference-number banner.
 */
(function () {
  "use strict";

  if (!window.WS_ENDPOINT) {
    console.warn("chat-widget: window.WS_ENDPOINT not set; aborting init");
    return;
  }

  // ── State ────────────────────────────────────────────────────
  const sessionId = (function () {
    const stored = sessionStorage.getItem("rcac_chat_session");
    if (stored && /^[a-z0-9]{12}$/.test(stored)) return stored;
    const fresh = Array.from(crypto.getRandomValues(new Uint8Array(6)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    sessionStorage.setItem("rcac_chat_session", fresh);
    return fresh;
  })();

  let ws = null;
  let isOpen = false;
  let activeAssistantBubble = null;
  let pendingFlush = "";
  let flushScheduled = false;
  let pendingHouseNumber = null;
  let suppressDeltas = false;

  // ── DOM scaffold ─────────────────────────────────────────────
  const root = document.createElement("div");
  root.id = "rcac-chat-root";
  root.innerHTML = `
    <button id="rcac-chat-toggle" aria-label="Open chat">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
        <path d="M12 2C6.48 2 2 6.04 2 11c0 2.74 1.42 5.18 3.69 6.84L4 22l4.59-1.45c1.06.29 2.2.45 3.41.45 5.52 0 10-4.04 10-9s-4.48-9-10-9z"/>
      </svg>
    </button>
    <div id="rcac-chat-panel" hidden>
      <header>
        <span>Riverside County Auditor-Controller</span>
        <div class="rcac-header-actions">
          <button id="rcac-chat-restart" aria-label="Start over" title="Start a new conversation (clears history and verification)">↻</button>
          <button id="rcac-chat-close" aria-label="Close chat">×</button>
        </div>
      </header>
      <div id="rcac-chat-messages" role="log" aria-live="polite"></div>
      <div id="rcac-chat-handoff" hidden></div>
      <form id="rcac-chat-form">
        <input id="rcac-chat-input" type="text" placeholder="Ask a question…" autocomplete="off" maxlength="2000" />
        <button type="submit" aria-label="Send">Send</button>
      </form>
    </div>
  `;
  document.body.appendChild(root);

  const toggleBtn = root.querySelector("#rcac-chat-toggle");
  const panel = root.querySelector("#rcac-chat-panel");
  const closeBtn = root.querySelector("#rcac-chat-close");
  const restartBtn = root.querySelector("#rcac-chat-restart");
  const messagesEl = root.querySelector("#rcac-chat-messages");
  const handoffEl = root.querySelector("#rcac-chat-handoff");
  const form = root.querySelector("#rcac-chat-form");
  const input = root.querySelector("#rcac-chat-input");

  // ── Bubble helpers ───────────────────────────────────────────

  // Linkify auto-detects http(s) URLs and tel: numbers in plain text and
  // renders them as <a> tags. Everything else is rendered as text nodes so we
  // never inject untrusted HTML from the model.
  const URL_PATTERN = /(https?:\/\/[^\s<>"]+)/g;
  function appendLinkified(parent, text) {
    let lastIndex = 0;
    let match;
    URL_PATTERN.lastIndex = 0;
    while ((match = URL_PATTERN.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const a = document.createElement("a");
      a.href = match[0];
      a.textContent = match[0];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      parent.appendChild(a);
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      parent.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
  }

  function addBubble(role, text) {
    const div = document.createElement("div");
    div.className = `rcac-bubble rcac-bubble-${role}`;
    appendLinkified(div, text);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function scheduleFlush() {
    if (flushScheduled) return;
    flushScheduled = true;
    requestAnimationFrame(() => {
      if (activeAssistantBubble && pendingFlush) {
        // For streaming, we accumulate plain text on the bubble and
        // re-linkify on every flush. Slightly wasteful but simple, and keeps
        // partial URLs from being rendered as broken anchors mid-stream.
        const buffered = (activeAssistantBubble._raw || "") + pendingFlush;
        activeAssistantBubble._raw = buffered;
        activeAssistantBubble.textContent = "";
        appendLinkified(activeAssistantBubble, buffered);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      pendingFlush = "";
      flushScheduled = false;
    });
  }

  function showHandoff(ref) {
    handoffEl.hidden = false;
    handoffEl.innerHTML = `
      <strong>Reference number: ${ref}</strong>
      <p>Call <a href="tel:+19519553800">(951) 955-3800</a> during office hours and quote this number — an agent will pick up where we left off.</p>
    `;
  }

  function renderStreetOptions(options) {
    const group = document.createElement("div");
    group.className = "rcac-street-options";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Street options");

    const label = document.createElement("div");
    label.className = "rcac-street-label";
    label.textContent = "Select your street:";
    group.appendChild(label);

    let selectedStreet = null;

    // Sliding number-input section (hidden until a street is chosen).
    const slideWrap = document.createElement("div");
    slideWrap.className = "rcac-number-slide";

    const numWrap = document.createElement("div");
    numWrap.className = "rcac-number-input-wrap";

    const numLabel = document.createElement("label");
    numLabel.textContent = "House / Unit Number";

    const numInput = document.createElement("input");
    numInput.type = "text";
    numInput.value = "123";
    numInput.autocomplete = "off";
    numInput.maxLength = 20;

    const actions = document.createElement("div");
    actions.className = "rcac-number-actions";

    const verifyBtn = document.createElement("button");
    verifyBtn.type = "button";
    verifyBtn.className = "rcac-verify-btn";
    verifyBtn.textContent = "Verify";
    verifyBtn.addEventListener("click", () => {
      const num = numInput.value.trim();
      if (!num || !selectedStreet) return;
      verifyBtn.disabled = true;
      // Stash the house number; send the street first so the server calls
      // tax_lookup(name, street). The number_input frame will auto-submit it.
      pendingHouseNumber = num;
      addBubble("user", selectedStreet);
      sendMessage(selectedStreet);
      input.disabled = true;
    });

    const cancelLink = document.createElement("a");
    cancelLink.href = "#";
    cancelLink.textContent = "Cancel";
    cancelLink.style.cssText = "font-size:0.75rem; color:#556575; text-decoration:underline; cursor:pointer;";
    cancelLink.addEventListener("click", (e) => {
      e.preventDefault();
      group.querySelectorAll(".rcac-street-btn").forEach((b) => {
        b.disabled = false;
        b.classList.remove("rcac-street-btn--selected");
      });
      slideWrap.classList.remove("open");
      selectedStreet = null;
      pendingHouseNumber = null;
    });

    actions.appendChild(verifyBtn);
    actions.appendChild(cancelLink);
    numWrap.appendChild(numLabel);
    numWrap.appendChild(numInput);
    numWrap.appendChild(actions);
    slideWrap.appendChild(numWrap);

    options.forEach((street) => {
      const btn = document.createElement("button");
      btn.className = "rcac-street-btn";
      btn.type = "button";
      btn.textContent = street;
      btn.addEventListener("click", () => {
        group.querySelectorAll(".rcac-street-btn").forEach((b) => {
          b.disabled = true;
          b.classList.remove("rcac-street-btn--selected");
        });
        btn.classList.add("rcac-street-btn--selected");
        selectedStreet = street;
        slideWrap.classList.add("open");
        numInput.value = "";
        numInput.focus();
      });
      group.appendChild(btn);
    });

    group.appendChild(slideWrap);
    messagesEl.appendChild(group);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function renderNumberInput() {
    const wrap = document.createElement("div");
    wrap.className = "rcac-inline-number";

    const lbl = document.createElement("label");
    lbl.textContent = "Enter your house / unit number:";

    const numInput = document.createElement("input");
    numInput.type = "text";
    numInput.placeholder = "e.g. 789";
    numInput.autocomplete = "off";
    numInput.maxLength = 20;

    const submitBtn = document.createElement("button");
    submitBtn.type = "button";
    submitBtn.className = "rcac-verify-btn";
    submitBtn.textContent = "Submit";

    const doSubmit = () => {
      const num = numInput.value.trim();
      if (!num) return;
      submitBtn.disabled = true;
      numInput.disabled = true;
      addBubble("user", num);
      sendMessage(num);
      wrap.remove();
      input.disabled = true;
    };

    submitBtn.addEventListener("click", doSubmit);
    numInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doSubmit();
    });

    wrap.appendChild(lbl);
    wrap.appendChild(numInput);
    wrap.appendChild(submitBtn);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    numInput.focus();
  }

  // ── WebSocket lifecycle ──────────────────────────────────────
  function ensureSocket() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return ws;
    }
    ws = new WebSocket(`${window.WS_ENDPOINT}?session=${sessionId}`);
    ws.addEventListener("open", () => {
      isOpen = true;
    });
    ws.addEventListener("close", () => {
      isOpen = false;
    });
    ws.addEventListener("error", (e) => {
      console.warn("chat-widget: socket error", e);
    });
    ws.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      switch (frame.type) {
        case "delta":
          if (!suppressDeltas) {
            if (!activeAssistantBubble) activeAssistantBubble = addBubble("assistant", "");
            pendingFlush += frame.text;
            scheduleFlush();
          }
          break;
        case "tool_use":
          // optional: show a transient "looking that up..." hint
          break;
        case "street_options":
          if (Array.isArray(frame.options) && frame.options.length) {
            // Suppress any Claude commentary that accompanies the quiz frames.
            suppressDeltas = true;
            activeAssistantBubble = null;
            renderStreetOptions(frame.options);
          }
          break;
        case "number_input":
          suppressDeltas = true;
          activeAssistantBubble = null;
          if (pendingHouseNumber) {
            const num = pendingHouseNumber;
            pendingHouseNumber = null;
            addBubble("user", num);
            sendMessage(num);
          } else {
            renderNumberInput();
          }
          break;
        case "handoff":
          if (frame.reference) showHandoff(frame.reference);
          break;
        case "done":
          activeAssistantBubble = null;
          suppressDeltas = false;
          input.disabled = false;
          input.focus();
          break;
        case "error":
          activeAssistantBubble = null;
          suppressDeltas = false;
          addBubble("system", `⚠ ${frame.message || "Something went wrong."}`);
          input.disabled = false;
          break;
      }
    });
    return ws;
  }

  function sendMessage(text) {
    const sock = ensureSocket();
    const send = () => sock.send(JSON.stringify({
      action: "sendMessage",
      session: sessionId,
      text,
    }));
    if (sock.readyState === WebSocket.OPEN) {
      send();
    } else {
      sock.addEventListener("open", send, { once: true });
    }
  }

  function renderWelcome() {
    addBubble("assistant", "Welcome to the Riverside County Auditor-Controller's Office. I can help with unclaimed refunds, stale-dated warrants, payroll questions, and property tax. To get started, what's your name?");
  }

  function startOver() {
    // New session ID, new server-side state. Identity verification, transcript,
    // and any prior locks reset.
    sessionStorage.removeItem("rcac_chat_session");
    location.reload();
  }

  // ── UI events ────────────────────────────────────────────────
  toggleBtn.addEventListener("click", () => {
    panel.hidden = false;
    toggleBtn.hidden = true;
    ensureSocket();
    if (!messagesEl.firstChild) {
      renderWelcome();
    }
    input.focus();
  });
  closeBtn.addEventListener("click", () => {
    panel.hidden = true;
    toggleBtn.hidden = false;
  });
  restartBtn.addEventListener("click", () => {
    if (confirm("Start over? This clears the conversation and any identity verification.")) {
      startOver();
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addBubble("user", text);
    sendMessage(text);
    input.value = "";
    input.disabled = true;
  });
})();
