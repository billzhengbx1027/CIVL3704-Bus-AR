const BOUNDS = [
  [-33.9030, 151.1775],
  [-33.8370, 151.2325]
];
const ROUTE_PATH_COLOUR = "#8a2be2";
const REFRESH_MS = 5_000;
const OLD_POSITION_MS = 120_000;
const SETTINGS_KEY = "civl3704-map-settings-v1";
const DEFAULT_COLOURS = {
  early: "#4da3ff",
  on_time: "#39d98a",
  late: "#ff4d4f",
  no_delay_data: "#a7adb4"
};
const DEFAULT_SETTINGS = {
  mode: "reference",
  earlyMinutes: 1,
  lateMinutes: 3,
  colours: { ...DEFAULT_COLOURS }
};

const map = L.map("map", {
  zoomControl: true,
  attributionControl: true,
  preferCanvas: true,
  dragging: true,
  scrollWheelZoom: true,
  doubleClickZoom: true,
  touchZoom: true,
  boxZoom: true,
  keyboard: true
});
map.fitBounds(BOUNDS, { padding: [4, 4] });

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const markerLayer = L.layerGroup().addTo(map);
const stopLayer = L.layerGroup().addTo(map);
const controls = {
  route: document.getElementById("route-filter"),
  stop: document.getElementById("stop-filter"),
  showRoute: document.getElementById("show-route"),
  showStops: document.getElementById("show-stops"),
  mode: document.getElementById("mode-select"),
  early: document.getElementById("early-minutes"),
  late: document.getElementById("late-minutes"),
  custom: document.getElementById("custom-settings"),
  shapeStatus: document.getElementById("shape-status"),
  modeNote: document.getElementById("mode-note"),
  status: document.getElementById("status"),
  error: document.getElementById("error")
};
const colourInputs = {
  early: document.getElementById("colour-early"),
  on_time: document.getElementById("colour-on-time"),
  late: document.getElementById("colour-late"),
  no_delay_data: document.getElementById("colour-no-data")
};

let settings = loadSettings();
let snapshot = null;
let routeShape = null;
let routeShapeTrip = null;
let pendingShapeKey = null;
let shapeRequest = 0;
let labelsVisible = true;
let refreshInFlight = false;
let projectionMode = new URLSearchParams(window.location.search).get("projection") === "1";

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (!saved || typeof saved !== "object") return structuredClone(DEFAULT_SETTINGS);
    const colours = { ...DEFAULT_COLOURS };
    for (const status of Object.keys(colours)) {
      if (/^#[0-9a-f]{6}$/i.test(saved.colours?.[status])) colours[status] = saved.colours[status];
    }
    return {
      mode: saved.mode === "custom" ? "custom" : "reference",
      earlyMinutes: validMinutes(saved.earlyMinutes, 0.5, 60, 1),
      lateMinutes: validMinutes(saved.lateMinutes, 0.5, 120, 3),
      colours
    };
  } catch (_) {
    return structuredClone(DEFAULT_SETTINGS);
  }
}

function validMinutes(value, minimum, maximum, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum ? number : fallback;
}

function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (_) { /* storage optional */ }
}

function syncSettingsForm() {
  controls.mode.value = settings.mode;
  controls.early.value = settings.earlyMinutes;
  controls.late.value = settings.lateMinutes;
  for (const [status, input] of Object.entries(colourInputs)) input.value = settings.colours[status];
  controls.custom.hidden = settings.mode !== "custom";
  controls.modeNote.textContent = settings.mode === "reference"
    ? "Greater Sydney Bus Contract first/mid-stop departure window: early from 1 min; late from 6 min. Here it is applied to a next-stop prediction."
    : "Your thresholds and colours apply to predicted next-stop delay on this device.";
  const colours = settings.mode === "custom" ? settings.colours : DEFAULT_COLOURS;
  document.querySelectorAll("#legend [data-status]").forEach(dot => {
    dot.style.backgroundColor = colours[dot.dataset.status];
  });
}

function readSettingsForm() {
  settings.mode = controls.mode.value === "custom" ? "custom" : "reference";
  settings.earlyMinutes = validMinutes(controls.early.value, 0.5, 60, settings.earlyMinutes);
  settings.lateMinutes = validMinutes(controls.late.value, 0.5, 120, settings.lateMinutes);
  for (const [status, input] of Object.entries(colourInputs)) settings.colours[status] = input.value;
  saveSettings();
  syncSettingsForm();
  render();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function dataAgeMs(bus) {
  const positionTime = Number(bus.timestamp);
  if (!Number.isFinite(positionTime) || positionTime <= 0) return Infinity;
  const snapshotTime = Date.parse(snapshot?.snapshot_time || "");
  const snapshotAge = Number.isFinite(snapshotTime) ? Date.now() - snapshotTime : Infinity;
  return Math.max(0, Date.now() - positionTime * 1000, snapshotAge);
}

function ageText(milliseconds) {
  if (!Number.isFinite(milliseconds)) return "unknown age";
  if (milliseconds < 60_000) return `${Math.floor(milliseconds / 1000)} sec old`;
  return `${Math.floor(milliseconds / 60_000)} min old`;
}

function statusFor(bus) {
  if (dataAgeMs(bus) > OLD_POSITION_MS) return "stale";
  return classifyDelaySeconds(bus.delay_seconds, settings);
}

function colourFor(status) {
  if (status === "stale") return "#7f8a96";
  const palette = settings.mode === "custom" ? settings.colours : DEFAULT_COLOURS;
  return palette[status] || palette.no_delay_data;
}

function delayText(bus) {
  const delay = Number(bus.delay_seconds);
  if (bus.delay_seconds === null || bus.delay_seconds === "" || !Number.isFinite(delay)) return "No delay prediction";
  const total = Math.round(Math.abs(delay));
  const event = bus.delay_event_type === "departure" ? "departure" : bus.delay_event_type === "arrival" ? "arrival" : "stop time";
  if (total === 0) return `Predicted ${event} on schedule`;
  return `Predicted ${event} ${Math.floor(total / 60)} min ${total % 60} sec ${delay < 0 ? "early" : "late"}`;
}

function distanceMeters(first, second) {
  const radians = degrees => degrees * Math.PI / 180;
  const lat1 = radians(first.latitude), lat2 = radians(second.latitude);
  const deltaLat = lat2 - lat1;
  const deltaLon = radians(second.longitude - first.longitude);
  const a = Math.sin(deltaLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLon / 2) ** 2;
  return 6_371_000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function stopText(bus) {
  if (dataAgeMs(bus) <= OLD_POSITION_MS && bus.stop_status === "STOPPED_AT") {
    return `At ${escapeHtml(bus.current_stop_name || bus.current_stop_id || "a stop")} (feed reported)`;
  }
  if (bus.next_stop && dataAgeMs(bus) <= OLD_POSITION_MS) {
    const distance = distanceMeters(bus, bus.next_stop);
    if (distance <= 50) return `Near next stop (~${Math.round(distance)} m); stopping not confirmed`;
  }
  return "Stop arrival not confirmed";
}

function updateOptions(select, options, allLabel) {
  const previous = select.value;
  select.replaceChildren(new Option(allLabel, ""));
  for (const [value, label] of options) select.add(new Option(label, value));
  select.value = options.some(([value]) => value === previous) ? previous : "";
}

function updateFilters() {
  if (!snapshot) return;
  const routes = [...new Set(snapshot.buses.map(bus => String(bus.route)))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  updateOptions(controls.route, routes.map(route => [route, route]), "All routes");
  const stops = (snapshot.stops || []).map(stop => [String(stop.id), stop.name]);
  stops.sort((a, b) => a[1].localeCompare(b[1]));
  updateOptions(controls.stop, stops, "All next stops");
}

function visibleBuses() {
  if (!snapshot) return [];
  return snapshot.buses.filter(bus =>
    (!controls.route.value || String(bus.route) === controls.route.value) &&
    (!controls.stop.value || String(bus.next_stop_id) === controls.stop.value)
  );
}

function render() {
  if (!snapshot) return;
  const buses = visibleBuses();
  markerLayer.clearLayers();
  stopLayer.clearLayers();
  let oldCount = 0;

  for (const bus of buses) {
    const status = statusFor(bus);
    if (status === "stale") oldCount++;
    const marker = L.circleMarker([bus.latitude, bus.longitude], {
      radius: 8,
      color: "#ffffff",
      weight: 2,
      fillColor: colourFor(status),
      fillOpacity: status === "stale" ? 0.65 : 0.96
    });
    const stopName = bus.next_stop?.name || bus.next_stop_id || "unknown";
    marker.bindPopup(
      `<strong>Route ${escapeHtml(bus.route)}</strong><br>` +
      `${escapeHtml(bus.headsign || bus.route_long_name || "")}<br>` +
      `${escapeHtml(delayText(bus))}<br>` +
      `Next stop: ${escapeHtml(stopName)}<br>` +
      `${stopText(bus)}<br>` +
      `Position: ${escapeHtml(ageText(dataAgeMs(bus)))}${status === "stale" ? " · old data" : ""}`
    );
    marker.bindTooltip(escapeHtml(bus.route), {
      permanent: labelsVisible,
      direction: "top",
      offset: [0, -8],
      className: "bus-label"
    });
    marker.addTo(markerLayer);
  }

  if (controls.showStops.checked) {
    const uniqueStops = new Map();
    for (const bus of buses) if (bus.next_stop) uniqueStops.set(bus.next_stop.id, bus.next_stop);
    for (const stop of uniqueStops.values()) {
      L.circleMarker([stop.latitude, stop.longitude], {
        radius: 5, color: "#122e45", weight: 2, fillColor: "#ffffff", fillOpacity: 0.95
      }).bindPopup(`<strong>${escapeHtml(stop.name)}</strong><br>Upcoming stop for a bus on the map`).addTo(stopLayer);
    }
  }

  const source = snapshot.source === "demo" ? "DEMO · " : "";
  const updated = snapshot.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleTimeString() : "unknown";
  controls.status.textContent = `${source}${buses.length} of ${snapshot.count} buses shown · ${oldCount} old positions · snapshot ${updated}`;
}

function clearShape() {
  shapeRequest++;
  if (routeShape) map.removeLayer(routeShape);
  routeShape = null;
  routeShapeTrip = null;
  pendingShapeKey = null;
}

async function updateRouteShape() {
  const route = controls.route.value;
  if (!route || !controls.showRoute.checked) {
    clearShape();
    controls.shapeStatus.textContent = route ? "Scheduled route path hidden." : "Choose a route to see one scheduled trip path.";
    return;
  }
  if (snapshot?.source === "demo") {
    clearShape();
    controls.shapeStatus.textContent = "Demo vehicles have sample trip IDs. Scheduled route paths are available with live data.";
    return;
  }
  const candidates = [...new Set(visibleBuses().map(bus => bus.trip_id).filter(Boolean))];
  if (routeShape && candidates.includes(routeShapeTrip)) return;
  const requestKey = JSON.stringify([route, controls.stop.value, candidates]);
  if (pendingShapeKey === requestKey) return;
  clearShape();
  pendingShapeKey = requestKey;
  const request = shapeRequest;
  controls.shapeStatus.textContent = "Loading scheduled route path…";
  for (const tripId of candidates) {
    try {
      const response = await fetch(`/api/route-shape?trip_id=${encodeURIComponent(tripId)}`);
      if (request !== shapeRequest) return;
      if (!response.ok) continue;
      const shape = await response.json();
      if (request !== shapeRequest) return;
      if (!Array.isArray(shape.coordinates) || shape.coordinates.length < 2) continue;
      routeShape = L.polyline(shape.coordinates, { color: ROUTE_PATH_COLOUR, weight: 5, opacity: 0.95 }).addTo(map);
      routeShapeTrip = tripId;
      pendingShapeKey = null;
      controls.shapeStatus.textContent = `Purple line: one scheduled path for route ${route}. Other trips may vary.`;
      return;
    } catch (_) { /* Try another active trip */ }
  }
  if (request === shapeRequest) {
    pendingShapeKey = null;
    controls.shapeStatus.textContent = "No scheduled path available for the current buses on this route.";
  }
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const response = await fetch("/api/buses", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load bus data");
    snapshot = payload;
    updateFilters();
    render();
    updateRouteShape();
    controls.error.hidden = true;
  } catch (error) {
    controls.error.textContent = error.message;
    controls.error.hidden = false;
    if (!snapshot) controls.status.textContent = "Waiting for bus data…";
    else render();
  } finally {
    refreshInFlight = false;
  }
}

function setProjectionMode(enabled) {
  projectionMode = enabled;
  document.body.classList.toggle("projection-mode", enabled);
  setTimeout(() => map.invalidateSize(), 100);
}

function toggleLabels() {
  labelsVisible = !labelsVisible;
  markerLayer.eachLayer(marker => {
    const tooltip = marker.getTooltip();
    if (!tooltip) return;
    tooltip.options.permanent = labelsVisible;
    if (labelsVisible) marker.openTooltip();
    else marker.closeTooltip();
  });
}

controls.route.addEventListener("change", () => { render(); updateRouteShape(); });
controls.stop.addEventListener("change", () => { render(); updateRouteShape(); });
controls.showRoute.addEventListener("change", updateRouteShape);
controls.showStops.addEventListener("change", render);
controls.mode.addEventListener("change", readSettingsForm);
for (const input of [controls.early, controls.late, ...Object.values(colourInputs)]) {
  input.addEventListener("change", readSettingsForm);
}
document.getElementById("reset-settings").addEventListener("click", () => {
  settings = { ...structuredClone(DEFAULT_SETTINGS), mode: "custom" };
  saveSettings();
  syncSettingsForm();
  render();
});

document.addEventListener("keydown", event => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === "p") setProjectionMode(!projectionMode);
  if (key === "l") toggleLabels();
  if (key === "b") document.body.classList.toggle("no-basemap");
  if (key === "r") refresh();
  if (key === "f") {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  }
});

syncSettingsForm();
setProjectionMode(projectionMode);
refresh();
setInterval(refresh, REFRESH_MS);
