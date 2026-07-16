const state = {
  payload: null,
  selectedHex: null,
  sortMode: "signal",
  lastRefresh: null,
  rangeMapKey: "",
  refreshInFlight: false,
  refreshTimer: null,
  consecutiveFailures: 0,
  latencyMs: null,
};

const REFRESH_INTERVAL_MS = 5000;
const REQUEST_TIMEOUT_MS = 10000;
const MAX_BACKOFF_MS = 60000;

const $ = (id) => document.getElementById(id);

function hasValue(value) {
  return value !== null && value !== undefined && !Number.isNaN(Number(value));
}

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

function scheduleRefresh(delayMs) {
  if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
  state.refreshTimer = window.setTimeout(refresh, delayMs);
}

async function refresh() {
  if (state.refreshInFlight) return;
  state.refreshInFlight = true;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const startedAt = performance.now();
  try {
    const response = await fetch(`/api/status?ts=${Date.now()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    state.latencyMs = Math.round(performance.now() - startedAt);
    state.lastRefresh = Date.now();
    setText("refresh-age", "0");
    state.consecutiveFailures = 0;
    document.body.classList.remove("data-stale");
    render();
  } catch (error) {
    state.consecutiveFailures += 1;
    const message = error.name === "AbortError" ? "Status request timed out" : error.message;
    renderOffline(new Error(message));
  } finally {
    window.clearTimeout(timeout);
    state.refreshInFlight = false;
    const delay = state.consecutiveFailures
      ? Math.min(MAX_BACKOFF_MS, REFRESH_INTERVAL_MS * 2 ** state.consecutiveFailures)
      : REFRESH_INTERVAL_MS;
    scheduleRefresh(delay);
  }
}

function render() {
  const payload = state.payload;
  if (!payload) return;
  const system = payload.system || {};
  const adsb = payload.adsb || {};
  const totals = adsb.totals || {};
  const selected = chooseSelected(adsb.aircraft, adsb.selected);

  setText("station-name", system.hostname || "piaware");
  $("station-name").title = system.hostname || "piaware";
  setText("utc-clock", new Date().toISOString().slice(11, 19));
  setText("local-net", networkAddress(system.network, "local") || "pending");
  setText("tailscale-net", networkAddress(system.network, "tailscale") || "offline");
  const sourceLabel = adsb.demo ? "demo data" : adsb.available ? adsb.source : "data unavailable";
  setText("data-source", adsb.stale && adsb.available ? "stale data" : sourceLabel);
  setText("receiver-coords", receiverLabel(adsb.receiver));
  setText("aircraft-count", fmtNumber(totals.aircraft));
  setText("message-rate", fmtNumber(totals.messages_per_sec, 1));
  setText("max-range", fmtNumber(totals.max_range_nm, 1));
  setText("positioned-count", `${fmtNumber(totals.positioned)} positioned`);
  setText("cpu-temp", fmtNumber(system.temperature_c, 1));
  setText("uptime", `uptime ${fmtDuration(system.uptime_seconds)}`);
  setText("load-value", fmtNumber(system.load && system.load.one, 2));
  setText("memory-value", hasValue(system.memory && system.memory.percent) ? `${fmtNumber(system.memory.percent, 1)}%` : "--");
  setText("disk-value", hasValue(system.disk && system.disk.percent) ? `${fmtNumber(system.disk.percent, 1)}%` : "--");
  setText("api-latency", hasValue(state.latencyMs) ? `API ${fmtNumber(state.latencyMs)} ms` : "API pending");

  const cpuCount = Math.max(Number(system.cpu_count) || 1, 1);
  $("load-meter").max = cpuCount;
  $("load-meter").value = Math.min(Number(system.load && system.load.one) || 0, cpuCount);
  $("memory-meter").value = Number(system.memory && system.memory.percent) || 0;
  $("disk-meter").value = Number(system.disk && system.disk.percent) || 0;
  renderWifi(system.network && system.network.wifi);
  updateAgeIndicators();

  renderAircraftList(adsb.aircraft, selected);
  renderSelected(selected);
  renderServices(payload.services);
  renderSignal(adsb.signal || {});
  renderAlerts(payload.alerts);
  const mapAircraft = (adsb.aircraft || []).filter((item) => hasValue(item.lat) && hasValue(item.lon) && (!hasValue(item.seen_pos) || Number(item.seen_pos) <= 60));
  renderRange(mapAircraft, adsb.receiver, selected, payload.map);
  renderTimeline(adsb.history);
  renderMapAttribution(payload.map);
  renderFeedState(payload);
}

function renderOffline(error) {
  document.body.classList.add("data-stale");
  const staleSeconds = state.lastRefresh ? Math.floor((Date.now() - state.lastRefresh) / 1000) : null;
  setText("data-source", staleSeconds === null ? "offline" : `stale ${staleSeconds}s`);
  setText("feed-state", "offline");
  $("feed-state").className = "state-pill state-critical";
  const alertLog = $("alert-log");
  alertLog.replaceChildren();
  const row = document.createElement("div");
  row.className = "alert-row";
  const level = document.createElement("span");
  level.className = "alert-level alert-critical";
  level.textContent = "error";
  const message = document.createElement("p");
  message.textContent = `${error.message}; retrying automatically`;
  row.append(level, message);
  alertLog.append(row);
}

function networkAddress(network, kind) {
  for (const iface of (network && network.interfaces) || []) {
    for (const addr of iface.addresses || []) {
      const address = addr.split("/")[0];
      const isIpv4 = /^\d+\.\d+\.\d+\.\d+$/.test(address);
      const isTailscale = iface.name === "tailscale0" || address.startsWith("100.");
      const isPrivate = address.startsWith("10.") || address.startsWith("192.168.") || /^172\.(1[6-9]|2\d|3[01])\./.test(address);
      if (kind === "tailscale" && isIpv4 && isTailscale) return address;
      if (kind === "local" && isIpv4 && isPrivate && !isTailscale && iface.name !== "lo") return address;
    }
  }
  return "";
}

function renderWifi(wifi) {
  const signal = wifi && hasValue(wifi.signal_dbm) ? Number(wifi.signal_dbm) : null;
  $("wifi-meter").value = signal === null ? -90 : Math.max(-90, Math.min(-30, signal));
  const bitrate = wifi && hasValue(wifi.tx_bitrate_mbps) ? `${fmtNumber(wifi.tx_bitrate_mbps, 1)} Mb/s` : "rate unavailable";
  setText("wifi-value", signal === null ? (wifi && wifi.connected === false ? "offline" : "--") : `${fmtNumber(signal)} dBm`);
  $("wifi-value").title = bitrate;
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
    copy.sort((a, b) => (b.distance_nm ?? -1) - (a.distance_nm ?? -1));
  } else {
    copy.sort((a, b) => {
      const aFresh = a.seen !== undefined && a.seen <= 60 ? 1 : 0;
      const bFresh = b.seen !== undefined && b.seen <= 60 ? 1 : 0;
      if (aFresh !== bFresh) return bFresh - aFresh;
      return (b.rssi ?? -999) - (a.rssi ?? -999);
    });
  }
  return copy.slice(0, 12);
}

function appendAircraftCell(row, tag, text) {
  const cell = document.createElement(tag);
  cell.textContent = text;
  row.append(cell);
}

function renderAircraftList(aircraft, selected) {
  const list = $("aircraft-list");
  list.replaceChildren();
  const rows = sortedAircraft(aircraft);
  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "aircraft-row";
    appendAircraftCell(empty, "strong", "No tracks");
    appendAircraftCell(empty, "span", "--");
    appendAircraftCell(empty, "span", "--");
    appendAircraftCell(empty, "span", "--");
    list.append(empty);
    return;
  }
  for (const item of rows) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `aircraft-row ${selected && selected.hex === item.hex ? "selected" : ""}`;
    row.setAttribute("aria-pressed", selected && selected.hex === item.hex ? "true" : "false");
    appendAircraftCell(row, "strong", item.flight || item.hex || "UNKNOWN");
    appendAircraftCell(row, "span", hasValue(item.altitude) ? `${fmtNumber(item.altitude)} ft` : "-- ft");
    appendAircraftCell(row, "span", hasValue(item.distance_nm) ? `${fmtNumber(item.distance_nm, 1)} nm` : "-- nm");
    appendAircraftCell(row, "span", hasValue(item.rssi) ? `${fmtNumber(item.rssi, 1)} dB` : "-- dB");
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
  setText("selected-altitude", item && hasValue(item.altitude) ? `${fmtNumber(item.altitude)} ft` : "--");
  setText("selected-distance", item && hasValue(item.distance_nm) ? `${fmtNumber(item.distance_nm, 1)} nm` : "--");
  setText("selected-track", item && hasValue(item.track) ? `${fmtNumber(item.track, 0)} deg` : "--");
  setText("selected-rssi", item && hasValue(item.rssi) ? `${fmtNumber(item.rssi, 1)} dB` : "--");
  setText("selected-seen", item && item.seen !== undefined ? `${fmtNumber(item.seen, 1)}s` : "--");
}

function renderServices(services) {
  const list = $("service-list");
  list.replaceChildren();
  for (const service of services || []) {
    const row = document.createElement("div");
    const serviceClass = `service-${String(service.state || "unknown").toLowerCase()}`;
    row.className = "service-row";
    const name = document.createElement("span");
    name.textContent = service.required ? service.name : `${service.name} (optional)`;
    const status = document.createElement("strong");
    status.className = `service-state ${serviceClass}`;
    status.textContent = service.state || "unknown";
    row.append(name, status);
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
  log.replaceChildren();
  for (const alert of alerts || []) {
    const row = document.createElement("div");
    row.className = "alert-row";
    const level = document.createElement("span");
    level.className = `alert-level alert-${alert.level}`;
    level.textContent = alert.level;
    const message = document.createElement("p");
    message.textContent = alert.message;
    row.append(level, message);
    log.append(row);
  }
}

function renderFeedState(payload) {
  const adsb = payload.adsb || {};
  const services = payload.services || [];
  const critical = services.some((service) => service.required && service.state !== "active");
  const node = $("feed-state");
  if (!adsb.available) {
    node.textContent = "offline";
    node.className = "state-pill state-critical";
  } else if (adsb.stale || critical) {
    node.textContent = adsb.stale ? "stale" : "degraded";
    node.className = "state-pill state-warn";
  } else {
    node.textContent = "nominal";
    node.className = "state-pill state-ok";
  }
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

function renderRange(aircraft, receiver, selected, mapConfig) {
  const canvas = $("range-canvas");
  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.42;
  const maxDistance = Math.max(...(aircraft || []).map((item) => Number(item.distance_nm) || 0), 0);
  const maxNm = [20, 40, 100, 200, 300, 400].find((range) => maxDistance <= range) || Math.ceil(maxDistance / 100) * 100;
  setText("range-rings", `Range rings: ${fmtNumber(maxNm * 0.25)} / ${fmtNumber(maxNm * 0.5)} / ${fmtNumber(maxNm)} nm`);
  const mapProjection = renderBaseMap(receiver, mapConfig, cx, cy, radius, maxNm);

  ctx.strokeStyle = "rgba(201, 209, 217, 0.18)";
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
  ctx.strokeStyle = "rgba(63, 185, 80, 0.55)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  for (const item of aircraft || []) {
    if (!isFinite(item.lat) || !isFinite(item.lon) || !receiver) continue;
    const point = projectAircraft(item, receiver, cx, cy, radius, maxNm, mapProjection);
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
  ctx.fillText(`${fmtNumber(maxNm)} NM`, cx + radius - 52, cy - 8);
}

function renderMapAttribution(mapConfig) {
  const node = $("map-attribution");
  const visible = Boolean(mapConfig && mapConfig.enabled && mapConfig.attribution);
  node.hidden = !visible;
  if (!visible) return;
  node.textContent = mapConfig.attribution;
  if (mapConfig.attribution_url) node.href = mapConfig.attribution_url;
}

function renderBaseMap(receiver, mapConfig, cx, cy, radius, maxNm) {
  const map = $("range-map");
  if (!map) return null;

  const rLat = Number(receiver && receiver.lat);
  const rLon = Number(receiver && receiver.lon);
  const enabled = mapConfig && mapConfig.enabled && mapConfig.tile_url;
  if (!enabled || ![rLat, rLon].every(isFinite)) {
    map.classList.add("disabled");
    map.innerHTML = "";
    state.rangeMapKey = "";
    return null;
  }

  map.classList.remove("disabled");
  const zoom = chooseMapZoom(rLat, radius, maxNm);
  const center = mercatorPoint(rLat, rLon, zoom);
  const pxPerNm = pixelsPerNmAtZoom(rLat, zoom);
  const scale = radius / (maxNm * pxPerNm);
  const tileSize = 256;
  const scaledTile = tileSize * scale;
  const width = map.clientWidth;
  const height = map.clientHeight;
  const centerX = width / 2;
  const centerY = height / 2;
  const minTileX = Math.floor(center.x / tileSize - centerX / scaledTile) - 1;
  const maxTileX = Math.floor(center.x / tileSize + centerX / scaledTile) + 1;
  const minTileY = Math.floor(center.y / tileSize - centerY / scaledTile) - 1;
  const maxTileY = Math.floor(center.y / tileSize + centerY / scaledTile) + 1;
  const worldTiles = 2 ** zoom;
  const tiles = [];

  for (let x = minTileX; x <= maxTileX; x += 1) {
    for (let y = minTileY; y <= maxTileY; y += 1) {
      if (y < 0 || y >= worldTiles) continue;
      const wrappedX = ((x % worldTiles) + worldTiles) % worldTiles;
      tiles.push({
        url: tileUrl(mapConfig.tile_url, zoom, wrappedX, y),
        left: Math.round(centerX + (x * tileSize - center.x) * scale),
        top: Math.round(centerY + (y * tileSize - center.y) * scale),
      });
    }
  }

  const key = `${mapConfig.tile_url}|${zoom}|${scale.toFixed(4)}|${tiles.map((tile) => `${tile.url}@${tile.left},${tile.top}`).join(";")}`;
  if (key !== state.rangeMapKey) {
    map.replaceChildren(
      ...tiles.map((tile) => {
        const img = document.createElement("img");
        img.alt = "";
        img.src = tile.url;
        img.style.left = `${tile.left}px`;
        img.style.top = `${tile.top}px`;
        img.style.width = `${scaledTile}px`;
        img.style.height = `${scaledTile}px`;
        return img;
      }),
    );
    state.rangeMapKey = key;
  }

  return { center, zoom, scale };
}

function chooseMapZoom(lat, radius, maxNm) {
  const targetPxPerNm = radius / maxNm;
  const cosLat = Math.max(0.2, Math.cos((Number(lat) * Math.PI) / 180));
  const rawZoom = Math.log2((targetPxPerNm * cosLat * 40075016.686) / (1852 * 256));
  return Math.max(3, Math.min(11, Math.round(rawZoom)));
}

function pixelsPerNmAtZoom(lat, zoom) {
  const cosLat = Math.max(0.2, Math.cos((Number(lat) * Math.PI) / 180));
  return (1852 * 256 * 2 ** zoom) / (cosLat * 40075016.686);
}

function mercatorPoint(lat, lon, zoom) {
  const sinLat = Math.sin((Math.max(-85.05112878, Math.min(85.05112878, Number(lat))) * Math.PI) / 180);
  const scale = 256 * 2 ** zoom;
  return {
    x: ((Number(lon) + 180) / 360) * scale,
    y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale,
  };
}

function tileUrl(template, z, x, y) {
  return template.split("{z}").join(z).split("{x}").join(x).split("{y}").join(y);
}

function projectAircraft(item, receiver, cx, cy, radius, maxNm, mapProjection) {
  const lat = Number(item.lat);
  const lon = Number(item.lon);
  const rLat = Number(receiver.lat);
  const rLon = Number(receiver.lon);
  if (![lat, lon, rLat, rLon].every(isFinite)) return null;
  if (mapProjection) {
    const point = mercatorPoint(lat, lon, mapProjection.zoom);
    return {
      x: cx + (point.x - mapProjection.center.x) * mapProjection.scale,
      y: cy + (point.y - mapProjection.center.y) * mapProjection.scale,
    };
  }
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
  updateAgeIndicators();
}

function updateAgeIndicators() {
  const baseAge = state.payload && state.payload.adsb && state.payload.adsb.data_age_seconds;
  if (!hasValue(baseAge)) {
    setText("data-age-value", "--");
    $("data-age-meter").value = 30;
    return;
  }
  const elapsed = state.lastRefresh ? (Date.now() - state.lastRefresh) / 1000 : 0;
  const age = Math.max(0, Number(baseAge) + elapsed);
  setText("data-age-value", `${fmtNumber(age, age < 10 ? 1 : 0)}s`);
  $("data-age-meter").value = Math.min(age, 30);
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
tickClock();
refresh();
setInterval(tickClock, 1000);
