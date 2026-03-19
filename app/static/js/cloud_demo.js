(() => {
  const cfg = window.AGE_KIOSK_CLOUD_CONFIG || {};

  const el = {
    video: document.getElementById("cloud-video"),
    canvas: document.getElementById("cloud-canvas"),
    camChip: document.getElementById("cam-chip"),
    providerChip: document.getElementById("provider-chip"),
    status: document.getElementById("cloud-status"),
    btnStart: document.getElementById("btn-start"),
    btnStop: document.getElementById("btn-stop"),
    btnShot: document.getElementById("btn-shot"),
    faceCount: document.getElementById("face-count"),
    aiAge: document.getElementById("ai-age"),
    aiRange: document.getElementById("ai-range"),
    decisionCode: document.getElementById("decision-code"),
    decisionLabel: document.getElementById("decision-label"),
    manualVerify: document.getElementById("manual-verify"),
    resultMsg: document.getElementById("result-msg"),
  };

  let stream = null;
  let timer = null;
  let inFlight = false;
  let inferRetryTimer = null;

  const setText = (node, value) => {
    if (!node) return;
    node.textContent = value ?? "--";
  };

  const setStatus = (text, type = "info") => {
    if (!el.status) return;
    el.status.textContent = text;
    el.status.dataset.type = type;
  };

  const setCamState = (state) => {
    if (!el.camChip) return;
    el.camChip.textContent = state;
  };

  const stopLoop = () => {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
  };

  const stopCamera = () => {
    stopLoop();
    if (inferRetryTimer) {
      window.clearTimeout(inferRetryTimer);
      inferRetryTimer = null;
    }
    if (stream) {
      for (const track of stream.getTracks()) {
        track.stop();
      }
      stream = null;
    }
    if (el.video) {
      el.video.srcObject = null;
    }
    setCamState("IDLE");
    setStatus("鏡頭已停止");
  };

  const renderInfer = (data = {}) => {
    setText(el.providerChip, data.provider || "--");
    setText(el.faceCount, data.face_count ?? 0);
    setText(el.aiAge, data.ai_age ?? "--");
    setText(el.aiRange, data.ai_age_range ?? "--");
    setText(el.decisionCode, data.decision_code ?? "--");
    setText(el.decisionLabel, data.decision_label ?? "--");
    setText(el.manualVerify, data.needs_manual_verify ? "YES" : "NO");
    setText(el.resultMsg, data.message ?? "--");
  };

  const frameToBlob = async () => {
    if (!el.video || !el.canvas) return null;
    const srcW = el.video.videoWidth || 640;
    const srcH = el.video.videoHeight || 480;
    const maxSide = 640;
    const scale = Math.min(1, maxSide / Math.max(srcW, srcH));
    const w = Math.max(160, Math.round(srcW * scale));
    const h = Math.max(120, Math.round(srcH * scale));
    el.canvas.width = w;
    el.canvas.height = h;
    const ctx = el.canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(el.video, 0, 0, w, h);
    return new Promise((resolve) => {
      el.canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.86);
    });
  };

  const callInfer = async () => {
    if (!stream || inFlight || !cfg.inferApi) return;
    inFlight = true;
    try {
      const blob = await frameToBlob();
      if (!blob) {
        setStatus("無法擷取影像", "warn");
        return;
      }
      const form = new FormData();
      form.append("image", blob, "frame.jpg");
      const res = await fetch(cfg.inferApi, {
        method: "POST",
        body: form,
      });
      const raw = await res.text();
      let json = null;
      try {
        json = raw ? JSON.parse(raw) : null;
      } catch {
        json = null;
      }
      if (!res.ok || !json?.success) {
        const msg = json?.message || `HTTP ${res.status}`;
        if (res.status === 503) {
          setStatus(`模型預熱中，請稍候重試（${msg}）`, "warn");
        } else {
          setStatus(`推論失敗：${msg}`, "warn");
        }
        return;
      }
      renderInfer(json.data || {});
      setStatus("推論成功", "ok");
    } catch (err) {
      setStatus(`推論連線失敗：${String(err)}（服務可能喚醒中，3 秒後重試）`, "warn");
      if (!inferRetryTimer && stream) {
        inferRetryTimer = window.setTimeout(() => {
          inferRetryTimer = null;
          callInfer();
        }, 3000);
      }
    } finally {
      inFlight = false;
    }
  };

  const startCamera = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("瀏覽器不支援攝影機 API", "warn");
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "user",
          width: { ideal: 960 },
          height: { ideal: 540 },
        },
        audio: false,
      });
      if (el.video) {
        el.video.srcObject = stream;
        await el.video.play();
      }
      setCamState("LIVE");
      setStatus("鏡頭啟動成功");
      stopLoop();
      const interval = Math.max(600, Number(cfg.inferIntervalMs || 1200));
      timer = window.setInterval(callInfer, interval);
      callInfer();
    } catch (err) {
      setStatus(`鏡頭啟動失敗：${String(err)}`, "warn");
      setCamState("ERROR");
    }
  };

  const fetchHealth = async () => {
    if (!cfg.healthApi) return;
    try {
      const res = await fetch(cfg.healthApi, { cache: "no-store" });
      const json = await res.json();
      if (json?.success && json?.data) {
        setText(el.providerChip, json.data.provider_selected || "--");
        if (json.data.warming) {
          setStatus("模型預熱中，首次推論可能較慢", "warn");
        }
      }
    } catch {
      setStatus("無法讀取雲端狀態", "warn");
    }
  };

  if (el.btnStart) {
    el.btnStart.addEventListener("click", startCamera);
  }
  if (el.btnStop) {
    el.btnStop.addEventListener("click", stopCamera);
  }
  if (el.btnShot) {
    el.btnShot.addEventListener("click", callInfer);
  }

  window.addEventListener("beforeunload", stopCamera);
  fetchHealth();
})();
