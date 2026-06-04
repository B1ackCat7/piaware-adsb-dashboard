const state = {
  payload: null,
  selectedHex: null,
  sortMode: "signal",
  lastRefresh: null,
};

const $ = (id) => document.getElementById(id);

function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtBytes(value) {
  if (!value && value !== 0) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${fmtNumber(size, size >= 10 ? 0 : 1)} ${units[unit]}`;
}

function fmtDuration(seconds) {
  if (!seconds && seconds !== 0) return "--";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function configurePiAwareLinks() {
  const host = window.location.hostname || "localhost";
  const base = `${window.location.protocol}//${host}`;
  const links = [
    ["skyaware-link", `${base}/skyaware/`],
    ["piaware-link", `${base}/`],
    ["aircraft-json-link", `${base}/skyaware/data/aircraft.json`],
  ];
  for (const [id, href] of links) {
    const node = $(id);
    if (node) node.href = href;
  }
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "active" || normalized === "nominal" || normalized === "ok") return "state-ok";
  if (normalized === "critical" || normalized === "failed") return "state-critical";
  return "state-warn";
}

async function refresh() {
  try {
    const response = await fetch(`/api/status?ts=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    state.lastRefresh = Date.now();
    render();
  } catch (error) {
    renderOffline(error);
  }
}

function render() {
  const payload = state.payload;
  if (!payload) return;
  const system = payload.system;
  const adsb = payload.adsb;
  const totals = adsb.totals;
  const selected = chooseSelected(adsb.aircraft, adsb.selected);

  setText("station-name", system.hostname || "piaware");
  setText("utc-clock", new Date().toISOString().slice(11, 19));
  setText("local-net", networkAddress(system.network, "192.") || "pending");
  setText("tailscale-net", networkAddress(system.network, "100.") || "offline");
  setText("data-source", adsb.source === "demo" ? "demo data" : adsb.source);
  setText("receiver-coords", receiverLabel(adsb.receiver));
  setText("aircraft-count", fmtNumber(totals.aircraft));
  setText("message-rate", fmtNumber(totals.messages_per_sec, 1));
  setText("max-range", fmtNumber(totals.max_range_nm, 1));
  setText("positioned-count", `${fmtNumber(totals.positioned)} positioned`);
  setText("cpu-temp", fmtNumber(system.temperature_c, 1));
  setText("uptime", `uptime ${fmtDuration(system.uptime_seconds)}`);
  setText("load-value", fmtNumber(system.load.one, 2));
  setText("memory-value", `${fmtNumber(system.memory.percent, 1)}%`);
  setText("disk-value", `${fmtNumber(system.disk.percent, 1)}%`);

  $("load-meter").value = Math.min(system.load.one || 0, 4);
  $("memory-meter").value = system.memory.percent || 0;
  $("disk-meter").value = system.disk.percent || 0;

  renderAircraftList(adsb.aircraft, selected);
  renderSelected(selected);
  renderServices(payload.services);
  renderSignal(adsb.signal);
  renderAlerts(payload.alerts);
  renderRange(adsb.aircraft, adsb.receiver, selected);
  renderTimeline(adsb.history);
  renderFeedState(payload.services);
}

function renderOffline(error) {
  setText("data-source", "offline");
  setText("feed-state", "offline");
  $("feed-state").className = "state-pill state-critical";
  const alertLog = $("alert-log");
  alertLog.innerHTML = "";
  const row = document.createElement("div");
  row.className = "alert-row";
  row.innerHTML = `<span class="alert-level alert-critical">error</span><p>${error.message}</p>`;
  alertLog.append(row);
}

function networkAddress(network, prefix) {
  for (const iface of network.interfaces || []) {
    for (const addr of iface.addresses || []) {
      if (addr.startsWith(prefix)) return addr.split("/")[0];
    }
  }
  return "";
}

function receiverLabel(receiver) {
  if (!receiver || receiver.lat === undefined || receiver.lon === undefined) return "receiver pending";
  return `${fmtNumber(receiver.lat, 4)}, ${fmtNumber(receiver.lon, 4)}`;
}

function chooseSelected(aircraft, fallback) {
  if (!aircraft || aircraft.length === 0) return fallback;
  const existing = aircraft.find((item) => item.hex === state.selectedHex);
  const chosen = existing || sortedAircraft(aircraft)[0] || fallback || aircraft[0];
  state.selectedHex = chosen ? chosen.hex : null;
  return chosen;
}

function sortedAircraft(aircraft) {
  const copy = [...(aircraft || [])];
  if (state.sortMode === "range") {
    copy.sort((a, b) => (b.distance_nm || 0) - (a.distance_nm || 0));
  } else {
    copy.sort((a, b) => {
      const aFresh = a.seen !== undefined && a.seen <= 60 ? 1 : 0;
      const bFresh = b.seen !== undefined && b.seen <= 60 ? 1 : 0;
      if (aFresh !== bFresh) return bFresh - aFresh;
      return (b.rssi || -999) - (a.rssi || -999);
    });
  }
  return copy.slice(0, 12);
}

function renderAircraftList(aircraft, selected) {
  const list = $("aircraft-list");
  list.innerHTML = "";
  const rows = sortedAircraft(aircraft);
  if (rows.length === 0) {
    list.innerHTML = `<div class="aircraft-row"><strong>No tracks</strong><span>--</span><span>--</span><span>--</span></div>`;
    return;
  }
  for (const item of rows) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `aircraft-row ${selected && selected.hex === item.hex ? "selected" : ""}`;
    row.innerHTML = `
      <strong>${item.flight || item.hex || "UNKNOWN"}</strong>
      <span>${item.altitude ? fmtNumber(item.altitude) : "--"} ft</span>
      <span>${item.distance_nm ? fmtNumber(item.distance_nm, 1) : "--"} nm</span>
      <span>${item.rssi ? fmtNumber(item.rssi, 1) : "--"} dB</span>
    `;
    row.addEventListener("click", () => {
      state.selectedHex = item.hex;
      render();
    });
    list.append(row);
  }
}

function renderSelected(item) {
  setText("selected-flight", item ? item.flight || item.hex : "--");
  setText("selected-hex", item ? item.hex || "--" : "--");
  setText("selected-altitude", item && item.altitude ? `${fmtNumber(item.altitude)} ft` : "--");
  setText("selected-distance", item && item.distance_nm ? `${fmtNumber(item.distance_nm, 1)} nm` : "--");
  setText("selected-track", item && item.track ? `${fmtNumber(item.track, 0)} deg` : "--");
  setText("selected-rssi", item && item.rssi ? `${fmtNumber(item.rssi, 1)} dB` : "--");
  setText("selected-seen", item && item.seen !== undefined ? `${fmtNumber(item.seen, 1)}s` : "--");
}

function renderServices(services) {
  const list = $("service-list");
  list.innerHTML = "";
  for (const service of services || []) {
    const row = document.createElement("div");
    const serviceClass = `service-${String(service.state || "unknown").toLowerCase()}`;
    row.className = "service-row";
    row.innerHTML = `
      <span>${service.name}</span>
      <strong class="service-state ${serviceClass}">${service.state}</strong>
    `;
    list.append(row);
  }
}

function renderSignal(signal) {
  setText("gain-db", signal.gain_db === null || signal.gain_db === undefined ? "--" : `${fmtNumber(signal.gain_db, 1)} dB`);
  setText("signal-db", signal.signal_db === null || signal.signal_db === undefined ? "--" : `${fmtNumber(signal.signal_db, 1)} dB`);
  setText("noise-db", signal.noise_db === null || signal.noise_db === undefined ? "--" : `${fmtNumber(signal.noise_db, 1)} dB`);
  setText("peak-db", signal.peak_signal_db === null || signal.peak_signal_db === undefined ? "--" : `${fmtNumber(signal.peak_signal_db, 1)} dB`);
}

function renderAlerts(alerts) {
  const log = $("alert-log");
  log.innerHTML = "";
  for (const alert of alerts || []) {
    const row = document.createElement("div");
    row.className = "alert-row";
    row.innerHTML = `
      <span class="alert-level alert-${alert.level}">${alert.level}</span>
      <p>${alert.message}</p>
    `;
    log.append(row);
  }
}

function renderFeedState(services) {
  const critical = (services || []).some((service) => ["dump1090-fa", "piaware"].includes(service.name) && service.state !== "active");
  const node = $("feed-state");
  node.textContent = critical ? "degraded" : "nominal";
  node.className = `state-pill ${critical ? "state-warn" : "state-ok"}`;
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function renderRange(aircraft, receiver, selected) {
  const canvas = $("range-canvas");
  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.42;

  ctx.strokeStyle = "#242a32";
  ctx.lineWidth = 1;
  for (const ring of [0.25, 0.5, 1]) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius * ring, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.moveTo(cx - radius, cy);
  ctx.lineTo(cx + radius, cy);
  ctx.moveTo(cx, cy - radius);
  ctx.lineTo(cx, cy + radius);
  ctx.stroke();

  ctx.fillStyle = "#3fb950";
  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, Math.PI * 2);
  ctx.fill();

  const maxNm = 100;
  for (const item of aircraft || []) {
    if (!isFinite(item.lat) || !isFinite(item.lon) || !receiver) continue;
    const point = projectAircraft(item, receiver, cx, cy, radius, maxNm);
    if (!point) continue;
    const isSelected = selected && item.hex === selected.hex;
    ctx.fillStyle = isSelected ? "#d29922" : "#58a6ff";
    ctx.strokeStyle = isSelected ? "#d29922" : "rgba(88,166,255,0.55)";
    ctx.beginPath();
    ctx.arc(point.x, point.y, isSelected ? 4.5 : 3, 0, Math.PI * 2);
    ctx.fill();
    if (item.track !== undefined) {
      const heading = ((item.track - 90) * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(point.x, point.y);
      ctx.lineTo(point.x + Math.cos(heading) * 18, point.y + Math.sin(heading) * 18);
      ctx.stroke();
    }
  }

  ctx.fillStyle = "#6e7681";
  ctx.font = "11px SFMono-Regular, Menlo, monospace";
  ctx.fillText("N", cx - 3, cy - radius - 10);
  ctx.fillText("100 NM", cx + radius - 42, cy - 8);
}

function projectAircraft(item, receiver, cx, cy, radius, maxNm) {
  const lat = Number(item.lat);
  const lon = Number(item.lon);
  const rLat = Number(receiver.lat);
  const rLon = Number(receiver.lon);
  if (![lat, lon, rLat, rLon].every(isFinite)) return null;
  const nmPerLat = 60;
  const nmPerLon = 60 * Math.cos((rLat * Math.PI) / 180);
  const dxNm = (lon - rLon) * nmPerLon;
  const dyNm = (lat - rLat) * nmPerLat;
  const x = cx + (dxNm / maxNm) * radius;
  const y = cy - (dyNm / maxNm) * radius;
  return { x, y };
}

function renderTimeline(history) {
  const canvas = $("timeline-canvas");
  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const points = history || [];
  if (points.length < 2) return;
  const pad = 18;
  const maxAircraft = Math.max(...points.map((p) => p.aircraft || 0), 1);
  const maxRange = Math.max(...points.map((p) => p.max_range_nm || 0), 1);
  drawLine(ctx, points, "aircraft", maxAircraft, width, height, pad, "#58a6ff");
  drawLine(ctx, points, "max_range_nm", maxRange, width, height, pad, "#d29922");
  ctx.strokeStyle = "#242a32";
  ctx.beginPath();
  ctx.moveTo(pad, height - pad);
  ctx.lineTo(width - pad, height - pad);
  ctx.stroke();
}

function drawLine(ctx, points, key, max, width, height, pad, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = pad + (index / (points.length - 1)) * (width - pad * 2);
    const y = height - pad - ((point[key] || 0) / max) * (height - pad * 2);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function tickClock() {
  setText("utc-clock", new Date().toISOString().slice(11, 19));
  if (state.lastRefresh) {
    setText("refresh-age", Math.floor((Date.now() - state.lastRefresh) / 1000));
  }
}

$("sort-aircraft").addEventListener("click", () => {
  state.sortMode = state.sortMode === "signal" ? "range" : "signal";
  $("sort-aircraft").textContent = state.sortMode === "signal" ? "Sort: Signal" : "Sort: Range";
  render();
});

window.addEventListener("resize", () => {
  if (state.payload) render();
});

configurePiAwareLinks();
refresh();
setInterval(refresh, 5000);
setInterval(tickClock, 1000);
