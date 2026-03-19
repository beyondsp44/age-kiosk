(() => {
  const cfg = window.AGE_KIOSK_MONITOR_CONFIG || {};

  const el = {
    total: document.getElementById("m-total"),
    pass: document.getElementById("m-pass"),
    alarm: document.getElementById("m-alarm"),
    passRate: document.getElementById("m-pass-rate"),
    donut: document.getElementById("m-donut"),
    bars: document.getElementById("m-bars"),
    historyBody: document.getElementById("m-history-body"),
    updated: document.getElementById("m-updated"),
    prevBtn: document.getElementById("m-prev-page"),
    nextBtn: document.getElementById("m-next-page"),
    pageInfo: document.getElementById("m-page-info"),
    exportBtn: document.getElementById("m-export-csv"),
    dailyArchiveBtn: document.getElementById("m-archive-daily"),
    startDate: document.getElementById("m-date-start"),
    endDate: document.getElementById("m-date-end"),
    applyDateBtn: document.getElementById("m-date-apply"),
    clearDateBtn: document.getElementById("m-date-clear"),
    quickRangeBtns: Array.from(document.querySelectorAll("[data-range-preset]")),
  };

  const historyLimit = Math.max(1, Number(cfg.historyLimit || 10));
  let historyOffset = 0;
  let historyTotal = 0;
  let loadingHistory = false;
  let timer = null;
  let filterStartDate = "";
  let filterEndDate = "";
  let activeRangePreset = "all";

  const setText = (node, value) => {
    if (!node) return;
    node.textContent = value ?? "--";
  };

  const toDateString = (d) => {
    const dt = d instanceof Date ? d : new Date(d);
    const year = dt.getFullYear();
    const month = String(dt.getMonth() + 1).padStart(2, "0");
    const day = String(dt.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };

  const todayStr = () => toDateString(new Date());
  const fmtAge = (v) => (v === null || v === undefined ? "--" : `${v} 歲`);
  const levelClass = (status) => (String(status || "").startsWith("PASS") ? "pass" : "alarm");

  const startOfWeek = (d) => {
    const x = new Date(d);
    const day = (x.getDay() + 6) % 7; // Monday=0
    x.setDate(x.getDate() - day);
    return x;
  };

  const setActiveRangePreset = (preset) => {
    activeRangePreset = preset;
    if (!Array.isArray(el.quickRangeBtns)) return;
    el.quickRangeBtns.forEach((btn) => {
      const isActive = btn.dataset.rangePreset === preset;
      btn.classList.toggle("active", isActive);
    });
  };

  const getPresetRange = (preset) => {
    const now = new Date();
    const today = todayStr();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");

    if (preset === "today") {
      return { start: today, end: today };
    }
    if (preset === "week") {
      return { start: toDateString(startOfWeek(now)), end: today };
    }
    if (preset === "month") {
      return { start: `${year}-${month}-01`, end: today };
    }
    if (preset === "year") {
      return { start: `${year}-01-01`, end: today };
    }
    if (preset === "all") {
      return { start: "", end: "" };
    }
    return null;
  };

  const initDatePickerInput = (input) => {
    if (!input) return;

    const openPicker = () => {
      if (typeof input.showPicker === "function") {
        try {
          input.showPicker();
        } catch {
          // Ignore when browser blocks picker invocation.
        }
      }
    };

    // Single-click/focus opens date picker immediately.
    input.addEventListener("focus", openPicker);
    input.addEventListener("click", openPicker);
    input.addEventListener("pointerdown", () => {
      input.focus({ preventScroll: true });
      openPicker();
    });

    // Block manual typing/editing; keep only picker selection.
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") {
        event.preventDefault();
      }
    });
    input.addEventListener("beforeinput", (event) => event.preventDefault());
    input.addEventListener("paste", (event) => event.preventDefault());
    input.addEventListener("drop", (event) => event.preventDefault());
  };

  const buildParams = (extra = {}) => {
    const params = new URLSearchParams();
    Object.entries(extra).forEach(([k, v]) => {
      if (v !== undefined && v !== null && String(v) !== "") {
        params.set(k, String(v));
      }
    });
    if (filterStartDate) params.set("start_date", filterStartDate);
    if (filterEndDate) params.set("end_date", filterEndDate);
    return params;
  };

  const refreshExportLinks = () => {
    if (el.exportBtn && cfg.exportApi) {
      const params = buildParams({ limit: 0 });
      el.exportBtn.setAttribute("href", `${cfg.exportApi}?${params.toString()}`);
    }
    if (el.dailyArchiveBtn && cfg.dailyArchiveApi) {
      const dateForArchive = filterEndDate || filterStartDate || todayStr();
      const p = new URLSearchParams({ date: dateForArchive });
      el.dailyArchiveBtn.setAttribute("href", `${cfg.dailyArchiveApi}?${p.toString()}`);
    }
  };

  const updatePager = () => {
    const pages = Math.max(1, Math.ceil(Math.max(0, historyTotal) / historyLimit));
    const page = Math.min(pages, Math.floor(historyOffset / historyLimit) + 1);
    setText(el.pageInfo, `${page} / ${pages}`);

    if (el.prevBtn) {
      el.prevBtn.disabled = loadingHistory || historyOffset <= 0;
    }
    if (el.nextBtn) {
      el.nextBtn.disabled = loadingHistory || (historyOffset + historyLimit >= historyTotal);
    }
  };

  const renderCards = (summary = {}) => {
    const total = Number(summary.total_count || 0);
    const pass = Number(summary.pass_count || 0);
    const alarm = Number(summary.alarm_count || 0);
    const passRate = Number(summary.pass_rate_pct || 0);

    setText(el.total, total);
    setText(el.pass, pass);
    setText(el.alarm, alarm);
    setText(el.passRate, `PASS ${passRate.toFixed(1)}%`);

    if (el.donut) {
      const p = Math.max(0, Math.min(100, passRate));
      el.donut.style.background = `conic-gradient(#23d18b ${p}%, #e15469 ${p}% 100%)`;
      el.donut.setAttribute("aria-label", `PASS ${p.toFixed(1)}%, ALARM ${(100 - p).toFixed(1)}%`);
    }
  };

  const renderBars = (dist = {}) => {
    if (!el.bars) return;
    el.bars.innerHTML = "";

    const labels = ["0-12", "13-17", "18-25", "26-35", "36-45", "46-60", "61+"];
    const values = labels.map((k) => Number(dist[k] || 0));
    const maxVal = Math.max(1, ...values);

    const bucketClass = (label) => {
      if (label === "0-12" || label === "13-17") return "bar-risk-high";
      if (label === "18-25") return "bar-risk-mid";
      return "bar-risk-low";
    };

    labels.forEach((label, idx) => {
      const val = values[idx];
      const ratio = (val / maxVal) * 100;

      const item = document.createElement("div");
      item.className = "bar-item";

      const track = document.createElement("div");
      track.className = "bar-track";

      const bar = document.createElement("div");
      bar.className = `bar-col ${bucketClass(label)}`;
      bar.style.height = `${ratio}%`;
      bar.textContent = String(val);

      const cap = document.createElement("div");
      cap.className = "bar-label";
      cap.textContent = label;

      track.appendChild(bar);
      item.appendChild(track);
      item.appendChild(cap);
      el.bars.appendChild(item);
    });
  };

  const renderHistory = (rows = []) => {
    if (!el.historyBody) return;
    el.historyBody.innerHTML = "";

    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="5" class="empty-cell">目前沒有資料</td>';
      el.historyBody.appendChild(tr);
      return;
    }

    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.className = levelClass(r.final_status);
      tr.innerHTML = `
        <td>${r.timestamp || "--"}</td>
        <td>${r.mode || "--"}</td>
        <td>${fmtAge(r.ai_age)}</td>
        <td>${fmtAge(r.verified_age)}</td>
        <td>${r.final_status || "--"}</td>
      `;
      el.historyBody.appendChild(tr);
    });
  };

  const fetchSummary = async () => {
    if (!cfg.summaryApi) return;
    const params = buildParams({ history_limit: historyLimit });
    const res = await fetch(`${cfg.summaryApi}?${params.toString()}`, { cache: "no-store" });
    const json = await res.json();
    if (!json?.success || !json?.data) return;

    renderCards(json.data.summary || {});
    renderBars(json.data.age_distribution || {});

    const totalCount = Number(json.data.summary?.total_count || 0);
    historyTotal = totalCount;

    if (historyOffset === 0) {
      const rows = Array.isArray(json.data.recent_records) ? json.data.recent_records : [];
      renderHistory(rows);
    }
    updatePager();
    refreshExportLinks();

    const ts = new Date().toLocaleTimeString("zh-TW", { hour12: false });
    setText(el.updated, `更新 ${ts}`);
  };

  const fetchHistoryPage = async () => {
    if (!cfg.historyApi) return;
    loadingHistory = true;
    updatePager();
    try {
      const params = buildParams({ limit: historyLimit, offset: historyOffset });
      const res = await fetch(`${cfg.historyApi}?${params.toString()}`, { cache: "no-store" });
      const json = await res.json();
      if (!json?.success || !json?.data) return;
      const data = json.data;
      historyTotal = Number(data.total_count || historyTotal || 0);
      renderHistory(Array.isArray(data.items) ? data.items : []);
      updatePager();
      refreshExportLinks();
    } finally {
      loadingHistory = false;
      updatePager();
    }
  };

  const syncDateInputs = () => {
    if (el.startDate) el.startDate.value = filterStartDate;
    if (el.endDate) el.endDate.value = filterEndDate;
  };

  const applyRangePreset = async (preset) => {
    const range = getPresetRange(preset);
    if (!range) return;
    filterStartDate = range.start;
    filterEndDate = range.end;
    historyOffset = 0;
    syncDateInputs();
    setActiveRangePreset(preset);
    refreshExportLinks();
    await fetchSummary();
  };

  const applyDateFilter = async () => {
    const start = el.startDate?.value?.trim() || "";
    const end = el.endDate?.value?.trim() || "";

    if (start && end && start > end) {
      setText(el.updated, "日期範圍錯誤：起日不可晚於迄日");
      return;
    }

    filterStartDate = start;
    filterEndDate = end;
    historyOffset = 0;
    syncDateInputs();
    setActiveRangePreset("custom");
    refreshExportLinks();
    await fetchSummary();
  };

  const clearDateFilter = async () => {
    filterStartDate = "";
    filterEndDate = "";
    historyOffset = 0;
    syncDateInputs();
    setActiveRangePreset("all");
    refreshExportLinks();
    await fetchSummary();
  };

  const goPrev = async () => {
    if (loadingHistory || historyOffset <= 0) return;
    historyOffset = Math.max(0, historyOffset - historyLimit);
    if (historyOffset === 0) {
      await fetchSummary();
      return;
    }
    await fetchHistoryPage();
  };

  const goNext = async () => {
    if (loadingHistory) return;
    if (historyOffset + historyLimit >= historyTotal) return;
    historyOffset += historyLimit;
    await fetchHistoryPage();
  };

  const tick = async () => {
    if (document.hidden) return;
    try {
      await fetchSummary();
      if (historyOffset > 0) {
        await fetchHistoryPage();
      }
    } catch {
      setText(el.updated, "更新失敗");
    }
  };

  if (el.prevBtn) el.prevBtn.addEventListener("click", () => goPrev());
  if (el.nextBtn) el.nextBtn.addEventListener("click", () => goNext());
  if (el.applyDateBtn) el.applyDateBtn.addEventListener("click", () => applyDateFilter());
  if (el.clearDateBtn) el.clearDateBtn.addEventListener("click", () => clearDateFilter());
  if (Array.isArray(el.quickRangeBtns)) {
    el.quickRangeBtns.forEach((btn) => {
      btn.addEventListener("click", () => applyRangePreset(btn.dataset.rangePreset || "all"));
    });
  }
  initDatePickerInput(el.startDate);
  initDatePickerInput(el.endDate);

  setActiveRangePreset(activeRangePreset);
  syncDateInputs();
  refreshExportLinks();
  updatePager();
  tick();
  timer = window.setInterval(tick, Math.max(1500, Number(cfg.pollMs || 3000)));

  window.addEventListener("beforeunload", () => {
    if (timer) window.clearInterval(timer);
  });
})();
