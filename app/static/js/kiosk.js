(() => {
  const cfg = window.AGE_KIOSK_CONFIG || {};

  const el = {
    state: document.getElementById("state"),
    reasonMessage: document.getElementById("reason_message"),
    reasonCode: document.getElementById("reason_code"),
    aiAge: document.getElementById("ai_age"),
    finalStatus: document.getElementById("final_status"),
    finalAge: document.getElementById("final_age"),
    ocrMsg: document.getElementById("ocr_msg"),
    historyList: document.getElementById("history-list"),
    historyCount: document.getElementById("history-count"),
    resetBtn: document.getElementById("reset-btn"),
    streamWrap: document.querySelector(".stream-wrap"),
  };

  let latestState = "";
  let keySending = false;
  let pollTimer = null;
  let historyTick = 0;

  const setText = (node, value) => {
    if (!node) return;
    node.textContent = value ?? "--";
  };

  const ageRangeText = (age) => {
    if (age === null || age === undefined) return null;
    const n = Number(age);
    if (Number.isNaN(n)) return null;
    const center = Math.round(n);
    const low = Math.max(0, center - 3);
    const high = center + 3;
    return `${low}-${high} 歲`;
  };

  const stateLabelText = (state, payload = {}) => {
    if (state === "WAITING" && String(payload.reason_code || "") === "NO_FACE") {
      return "WAITING_FOR_PERSON";
    }
    if (state === "OCR_PENDING") return "DOCUMENT_CAPTURE";
    if (state === "DOC_CAPTURED") return "DOC_CAPTURE_PREVIEW";
    if (state === "MANUAL_INPUT") return "BIRTHDAY_INPUT";
    return state || "--";
  };

  const renderStatus = (payload = {}) => {
    latestState = payload.state || "";

    const isFaceStage = ["WAITING", "ANALYZING", "PROCESSING"].includes(latestState);
    const faceDetected = Boolean(payload.face_detected);
    if (el.streamWrap) {
      el.streamWrap.classList.toggle("scan-active", isFaceStage && faceDetected);
    }

    setText(el.state, stateLabelText(latestState, payload));
    setText(el.reasonMessage, payload.reason_message || "--");
    setText(el.reasonCode, payload.reason_code || "--");

    const aiAgeText = payload.ai_age_range || ageRangeText(payload.ai_age) || "--";
    setText(el.aiAge, aiAgeText);
    setText(el.finalStatus, payload.final_status || "--");

    const finalAgeText = payload.final_age !== null && payload.final_age !== undefined ? `${payload.final_age} 歲` : "--";
    setText(el.finalAge, finalAgeText);
    setText(el.ocrMsg, payload.ocr_msg || "--");
  };

  const fetchJson = async (url, options) => {
    const res = await fetch(url, options);
    return res.json();
  };

  const fetchStatus = async () => {
    if (!cfg.statusApi) return;
    try {
      const data = await fetchJson(cfg.statusApi, { cache: "no-store" });
      if (data?.success && data?.data) {
        renderStatus(data.data);
      }
    } catch {
      setText(el.reasonMessage, "status fetch failed");
    }
  };

  const normalizeStatusClass = (level) => {
    if (level === "pass") return "pass";
    if (level === "alarm") return "alarm";
    return "unknown";
  };

  const rowText = (item) => {
    const status = String(item.final_status || "").toUpperCase();
    const isVerifiedPass = status.startsWith("PASS");
    let ai = "AI --";
    if (item.ai_age !== null && item.ai_age !== undefined) {
      const aiNum = Number(item.ai_age);
      if (Number.isFinite(aiNum)) {
        ai = isVerifiedPass ? `AI ${Math.round(aiNum)} 歲` : `AI ${ageRangeText(aiNum) || "--"}`;
      }
    }
    const verified =
      item.verified_age !== null && item.verified_age !== undefined ? `核驗 ${item.verified_age} 歲` : "核驗 --";
    return `${ai} | ${verified}`;
  };

  const renderHistory = (payload = {}) => {
    const items = Array.isArray(payload.items) ? payload.items : [];
    setText(el.historyCount, `${items.length} 筆`);
    if (!el.historyList) return;
    el.historyList.innerHTML = "";

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "目前尚無偵測紀錄";
      el.historyList.appendChild(empty);
      return;
    }

    for (const item of items) {
      const row = document.createElement("article");
      row.className = `history-row ${normalizeStatusClass(item.level)}`;

      const line1 = document.createElement("div");
      line1.className = "history-row-top";

      const status = document.createElement("span");
      status.className = "history-badge";
      status.textContent = item.final_status || "--";

      const ts = document.createElement("time");
      ts.className = "history-time";
      ts.textContent = item.timestamp || "--";

      line1.appendChild(ts);
      line1.appendChild(status);

      const line2 = document.createElement("div");
      line2.className = "history-row-mid";
      line2.textContent = rowText(item);

      const line3 = document.createElement("div");
      line3.className = "history-row-bottom";
      line3.textContent = item.mode ? `模式：${item.mode}` : "模式：--";

      row.appendChild(line1);
      row.appendChild(line2);
      row.appendChild(line3);
      el.historyList.appendChild(row);
    }
  };

  const fetchHistory = async () => {
    if (!cfg.historyApi) return;
    const limit = Math.max(1, Number(cfg.historyLimit || 5));
    const url = `${cfg.historyApi}?limit=${encodeURIComponent(limit)}`;
    try {
      const data = await fetchJson(url, { cache: "no-store" });
      if (data?.success && data?.data) {
        renderHistory(data.data);
      }
    } catch {
      if (el.historyList && !el.historyList.children.length) {
        const empty = document.createElement("div");
        empty.className = "history-empty";
        empty.textContent = "偵測紀錄讀取失敗";
        el.historyList.appendChild(empty);
      }
    }
  };

  const sendKey = async (key) => {
    if (!cfg.keyApi || keySending) return;
    keySending = true;
    try {
      await fetchJson(cfg.keyApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      await fetchStatus();
    } catch {
      setText(el.reasonMessage, "key send failed");
    } finally {
      keySending = false;
    }
  };

  const shouldSendKey = (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey) return false;
    if (latestState !== "MANUAL_INPUT") return false;
    const key = event.key;
    return /^[0-9]$/.test(key) || key === "Backspace" || key === "Enter" || key === "c" || key === "C";
  };

  window.addEventListener("keydown", (event) => {
    if (!shouldSendKey(event)) return;
    event.preventDefault();
    sendKey(event.key);
  });

  if (el.resetBtn) {
    el.resetBtn.addEventListener("click", async () => {
      if (!cfg.resetApi) return;
      try {
        await fetchJson(cfg.resetApi, { method: "POST" });
        await fetchStatus();
      } catch {
        setText(el.reasonMessage, "reset failed");
      }
    });
  }

  const pollTick = async () => {
    if (document.hidden) {
      return;
    }
    await fetchStatus();
    historyTick += 1;
    if (historyTick % 3 === 1) {
      await fetchHistory();
    }
  };

  pollTick();
  pollTimer = window.setInterval(pollTick, Math.max(500, Number(cfg.pollMs || 1000)));

  window.addEventListener("beforeunload", () => {
    if (pollTimer) {
      window.clearInterval(pollTimer);
    }
  });
})();
