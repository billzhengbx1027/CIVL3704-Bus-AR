// Replaced by /api/map-area (map_area.json) once it loads.
let mapArea = { south: -33.909, west: 151.106, north: -33.814, east: 151.271 };
const ROUTE_PATH_COLOUR = "#765bb2";
const REFRESH_MS = 5_000;
// Permanent labels are DOM elements; beyond this many they make zooming slow.
const LABEL_LIMIT = 150;
const LABEL_MIN_ZOOM = 15;
const OLD_POSITION_MS = 120_000;
const SETTINGS_KEY = "civl3704-map-settings-v1";
const PANEL_POSITION_KEY = "civl3704-panel-position-v1";
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
  keyboard: true,
  zoomSnap: 0.25
});
map.zoomControl.setPosition("topright");

function areaBounds() {
  return L.latLngBounds([mapArea.south, mapArea.west], [mapArea.north, mapArea.east]);
}

function fitMapArea() {
  map.fitBounds(areaBounds(), { padding: projectionMode ? [0, 0] : [4, 4], animate: false });
}

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const markerLayer = L.layerGroup().addTo(map);
const stopLayer = L.layerGroup().addTo(map);
const journeyStopLayer = L.layerGroup().addTo(map);
const panel = document.getElementById("panel");
const panelHeader = panel.querySelector(".panel-header");
const controls = {
  transport: document.getElementById("transport-filter"),
  route: document.getElementById("route-filter"),
  stop: document.getElementById("stop-filter"),
  showRoute: document.getElementById("show-route"),
  showStops: document.getElementById("show-stops"),
  filterStatus: document.getElementById("filter-status"),
  mode: document.getElementById("mode-select"),
  early: document.getElementById("early-minutes"),
  late: document.getElementById("late-minutes"),
  custom: document.getElementById("custom-settings"),
  shapeStatus: document.getElementById("shape-status"),
  modeNote: document.getElementById("mode-note"),
  status: document.getElementById("status"),
  feedStatus: document.getElementById("feed-status"),
  error: document.getElementById("error")
};
const transportChips = [...document.querySelectorAll(".transport-chip")];
const journeyControls = {
  form: document.getElementById("journey-form"),
  mode: document.getElementById("journey-mode"),
  from: document.getElementById("journey-from"),
  to: document.getElementById("journey-to"),
  fromOptions: document.getElementById("journey-from-options"),
  toOptions: document.getElementById("journey-to-options"),
  pickButtons: [...document.querySelectorAll(".journey-pick")],
  clear: document.getElementById("journey-clear"),
  status: document.getElementById("journey-status"),
  results: document.getElementById("journey-results")
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
let routeShapeKey = null;
let pendingShapeKey = null;
let shapeRequest = 0;
const tripShapeCache = new Map();
const knownStops = new Map();
const vehicleMarkers = new Map();
let currentJourney = null;
let journeyRequestId = 0;
let lastJourneyFetch = 0;
let pickingJourneyPoint = null;
let labelsVisible = true;
let refreshInFlight = false;
let projectionMode = new URLSearchParams(window.location.search).get("projection") === "1";
let panelDrag = null;

function setPanelPosition(left, top, save = false) {
  const bounds = panel.getBoundingClientRect();
  const margin = 8;
  const maxLeft = Math.max(margin, window.innerWidth - bounds.width - margin);
  const maxTop = Math.max(margin, window.innerHeight - bounds.height - margin);
  const x = Math.min(Math.max(left, margin), maxLeft);
  const y = Math.min(Math.max(top, margin), maxTop);
  panel.style.left = `${x}px`;
  panel.style.top = `${y}px`;
  panel.style.right = "auto";
  panel.style.bottom = "auto";
  panel.classList.add("panel-moved");
  if (save) {
    try { localStorage.setItem(PANEL_POSITION_KEY, JSON.stringify({ left: x, top: y })); }
    catch (_) { /* storage optional */ }
  }
}

function resetPanelPosition() {
  for (const property of ["left", "top", "right", "bottom"]) panel.style[property] = "";
  panel.classList.remove("panel-moved", "dragging");
  try { localStorage.removeItem(PANEL_POSITION_KEY); } catch (_) { /* storage optional */ }
}

function restorePanelPosition() {
  try {
    const position = JSON.parse(localStorage.getItem(PANEL_POSITION_KEY));
    if (Number.isFinite(position?.left) && Number.isFinite(position?.top)) {
      setPanelPosition(position.left, position.top);
    }
  } catch (_) { /* keep the default placement */ }
}

panelHeader.addEventListener("pointerdown", event => {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const bounds = panel.getBoundingClientRect();
  panelDrag = {
    id: event.pointerId, startX: event.clientX, startY: event.clientY,
    left: bounds.left, top: bounds.top, moved: false
  };
  panelHeader.setPointerCapture(event.pointerId);
  event.preventDefault();
});

panelHeader.addEventListener("pointermove", event => {
  if (!panelDrag || event.pointerId !== panelDrag.id) return;
  const dx = event.clientX - panelDrag.startX;
  const dy = event.clientY - panelDrag.startY;
  if (!panelDrag.moved && Math.hypot(dx, dy) < 3) return;
  panelDrag.moved = true;
  panel.classList.add("dragging");
  setPanelPosition(panelDrag.left + dx, panelDrag.top + dy);
  event.preventDefault();
});

function finishPanelDrag(event) {
  if (!panelDrag || event.pointerId !== panelDrag.id) return;
  if (panelDrag.moved) {
    const bounds = panel.getBoundingClientRect();
    setPanelPosition(bounds.left, bounds.top, true);
  }
  panelDrag = null;
  panel.classList.remove("dragging");
  if (panelHeader.hasPointerCapture(event.pointerId)) panelHeader.releasePointerCapture(event.pointerId);
}

panelHeader.addEventListener("pointerup", finishPanelDrag);
panelHeader.addEventListener("pointercancel", finishPanelDrag);
panelHeader.addEventListener("keydown", event => {
  const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  const direction = moves[event.key];
  if (!direction) return;
  event.preventDefault();
  const bounds = panel.getBoundingClientRect();
  const step = event.shiftKey ? 50 : 20;
  setPanelPosition(bounds.left + direction[0] * step, bounds.top + direction[1] * step, true);
});
document.getElementById("reset-panel-position").addEventListener("click", resetPanelPosition);
window.addEventListener("resize", () => {
  if (!panel.classList.contains("panel-moved") || !panel.getClientRects().length) return;
  const bounds = panel.getBoundingClientRect();
  setPanelPosition(bounds.left, bounds.top);
});

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
    ? "Bus reference: early from 1 min, late from 6 min at the next predicted stop. Train, metro and light rail colours use the same display thresholds, not an official punctuality measure."
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
  if (bus.valid === false || dataAgeMs(bus) > OLD_POSITION_MS) return "stale";
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
  if (bus.valid === false) return "Position is from an earlier feed update";
  if (dataAgeMs(bus) <= OLD_POSITION_MS && bus.stop_status === "STOPPED_AT") {
    return `At ${escapeHtml(bus.current_stop_name || bus.current_stop_id || "a stop")} (feed reported)`;
  }
  if (bus.next_stop && dataAgeMs(bus) <= OLD_POSITION_MS) {
    const distance = distanceMeters(bus, bus.next_stop);
    if (distance <= 50) return `Near next stop (~${Math.round(distance)} m); stopping not confirmed`;
  }
  return "Stop arrival not confirmed";
}

function updateOptions(select, options, allLabel, preserveMissing = false) {
  const previous = select.value;
  // Replacing options closes an open dropdown, so only do it when the list changed.
  const optionsKey = JSON.stringify([allLabel, options]);
  if (select.dataset.optionsKey === optionsKey) return;
  if (document.activeElement === select) return;
  select.dataset.optionsKey = optionsKey;
  const previousLabel = select.selectedOptions[0]?.textContent || previous;
  select.replaceChildren(new Option(allLabel, ""));
  for (const [value, label] of options) select.add(new Option(label, value));
  const available = options.some(([value]) => value === previous);
  if (previous && !available && preserveMissing) {
    select.add(new Option(`${previousLabel.replace(/ \(no current vehicles\)$/, "")} (no current vehicles)`, previous));
  }
  select.value = previous && (available || preserveMissing) ? previous : "";
}

function updateFilters() {
  if (!snapshot) return;
  const modeVehicles = snapshot.vehicles.filter(vehicle => controls.transport.value === "all" || vehicle.mode === controls.transport.value);
  const routes = [...new Set(modeVehicles.map(vehicle => String(vehicle.route)))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  updateOptions(controls.route, routes.map(route => [route, route]), "All routes", true);
  const routeVehicles = modeVehicles.filter(vehicle => !controls.route.value || String(vehicle.route) === controls.route.value);
  const stopIds = [...new Set(routeVehicles.map(vehicle => String(vehicle.next_stop_id || "")).filter(Boolean))];
  const stops = stopIds.map(id => [id, knownStops.get(id)?.name || id]);
  stops.sort((a, b) => a[1].localeCompare(b[1]));
  updateOptions(controls.stop, stops, "All next stops", true);
}

function visibleBuses() {
  if (!snapshot) return [];
  return snapshot.vehicles.filter(bus =>
    (controls.transport.value === "all" || bus.mode === controls.transport.value) &&
    (!controls.route.value || String(bus.route) === controls.route.value) &&
    (!controls.stop.value || String(bus.next_stop_id) === controls.stop.value)
  );
}

function selectTransportMode(mode, refreshMap = true) {
  if (!transportChips.some(chip => chip.dataset.transport === mode)) return;
  controls.transport.value = mode;
  for (const chip of transportChips) {
    chip.setAttribute("aria-pressed", String(chip.dataset.transport === mode));
  }
  if (!refreshMap) return;
  controls.route.value = "";
  controls.stop.value = "";
  updateFilters();
  render();
  updateRouteShape();
}

function popupHtml(bus) {
  const status = statusFor(bus);
  const stopName = bus.next_stop?.name || bus.next_stop_id || "unknown";
  const modeName = bus.mode === "light_rail" ? "Light rail" : bus.mode === "train" ? "Train" : bus.mode === "metro" ? "Metro" : "Bus";
  return `<strong>${modeName} ${escapeHtml(bus.route)}</strong><br>` +
    `${escapeHtml(bus.headsign || bus.route_long_name || "")}<br>` +
    `${escapeHtml(delayText(bus))}<br>` +
    `Next stop: ${escapeHtml(stopName)}<br>` +
    `${stopText(bus)}<br>` +
    `Position: ${escapeHtml(ageText(dataAgeMs(bus)))}${status === "stale" ? " · old data" : ""}`;
}

function railIcon(mode, status) {
  return L.divIcon({
    className: "rail-vehicle-icon",
    html: `<span class="rail-vehicle rail-vehicle--${mode}${status === "stale" ? " rail-vehicle--stale" : ""}" style="background-color:${colourFor(status)}"></span>`,
    iconSize: [24, 14],
    iconAnchor: [12, 7],
    popupAnchor: [0, -7]
  });
}

function permanentLabelsWanted(count) {
  return labelsVisible && (count <= LABEL_LIMIT || map.getZoom() >= LABEL_MIN_ZOOM);
}

function applyLabel(entry, permanent) {
  if (entry.permanentLabel === permanent && entry.labelText === entry.bus.route) return;
  entry.marker.unbindTooltip();
  entry.marker.bindTooltip(escapeHtml(entry.bus.route), {
    permanent, direction: "top", offset: [0, -8], className: "bus-label"
  });
  entry.permanentLabel = permanent;
  entry.labelText = entry.bus.route;
}

function updateLabels() {
  const permanent = permanentLabelsWanted(vehicleMarkers.size);
  for (const entry of vehicleMarkers.values()) applyLabel(entry, permanent);
}

// Markers are kept between refreshes and only moved or recoloured; rebuilding
// hundreds of markers, labels and popups every few seconds made the page stutter.
function renderVehicles(buses) {
  const seen = new Set();
  let oldCount = 0;
  const permanent = permanentLabelsWanted(buses.length);
  for (const [index, bus] of buses.entries()) {
    const key = `${bus.mode}:${bus.vehicle_id || bus.trip_id || index}`;
    seen.add(key);
    const status = statusFor(bus);
    if (status === "stale") oldCount++;
    const rail = ["train", "metro", "light_rail"].includes(bus.mode);
    let entry = vehicleMarkers.get(key);
    if (!entry) {
      const marker = rail
        ? L.marker([bus.latitude, bus.longitude], { icon: railIcon(bus.mode, status) })
        : L.circleMarker([bus.latitude, bus.longitude], {
            radius: 8, color: "#ffffff", weight: 2,
            fillColor: colourFor(status), fillOpacity: status === "stale" ? 0.65 : 0.96
          });
      entry = { marker, bus, rail, status, colour: colourFor(status) };
      marker.bindPopup(() => popupHtml(entry.bus));
      vehicleMarkers.set(key, entry);
      marker.addTo(markerLayer);
    } else {
      entry.bus = bus;
      const position = entry.marker.getLatLng();
      if (position.lat !== bus.latitude || position.lng !== bus.longitude) {
        entry.marker.setLatLng([bus.latitude, bus.longitude]);
      }
      const colour = colourFor(status);
      if (entry.status !== status || entry.colour !== colour) {
        if (entry.rail) entry.marker.setIcon(railIcon(bus.mode, status));
        else entry.marker.setStyle({ fillColor: colour, fillOpacity: status === "stale" ? 0.65 : 0.96 });
        entry.status = status;
        entry.colour = colour;
      }
      if (entry.marker.isPopupOpen()) entry.marker.getPopup().update();
    }
    applyLabel(entry, permanent);
  }
  for (const [key, entry] of vehicleMarkers) {
    if (seen.has(key)) continue;
    markerLayer.removeLayer(entry.marker);
    vehicleMarkers.delete(key);
  }
  return oldCount;
}

let stopLayerKey = "";

function renderStops(buses) {
  const uniqueStops = new Map();
  if (controls.showStops.checked) {
    for (const bus of buses) if (bus.next_stop) uniqueStops.set(bus.next_stop.id, bus.next_stop);
  }
  const selectedStop = knownStops.get(controls.stop.value);
  if (selectedStop) uniqueStops.set(selectedStop.id, selectedStop);
  const key = `${controls.stop.value}|${[...uniqueStops.keys()].sort().join(",")}`;
  if (key === stopLayerKey) return;
  stopLayerKey = key;
  stopLayer.clearLayers();
  for (const stop of uniqueStops.values()) {
    const selected = String(stop.id) === controls.stop.value;
    L.circleMarker([stop.latitude, stop.longitude], {
      radius: selected ? 9 : 5,
      color: selected ? "#4a3511" : "#122e45",
      weight: 2,
      fillColor: selected ? "#ffd166" : "#ffffff",
      fillOpacity: 0.95
    }).bindPopup(`<strong>${escapeHtml(stop.name)}</strong><br>${selected ? "Selected next stop" : "Upcoming stop for a vehicle on the map"}`).addTo(stopLayer);
  }
}

function render() {
  if (!snapshot) return;
  const buses = visibleBuses();
  const oldCount = renderVehicles(buses);
  renderStops(buses);
  const selectedStop = knownStops.get(controls.stop.value);

  if (buses.length) {
    controls.filterStatus.hidden = true;
  } else {
    const route = controls.route.value;
    const stop = controls.stop.value;
    const stopName = selectedStop?.name || controls.stop.selectedOptions[0]?.textContent?.replace(/ \(no current vehicles\)$/, "") || stop;
    const modeName = { bus: "bus", train: "train", metro: "metro", light_rail: "light rail" }[controls.transport.value];
    controls.filterStatus.textContent = route && stop
      ? `No current ${route} vehicle has ${stopName} as its next stop. Choose another stop or All next stops.`
      : route ? `No current vehicles are available for route ${route}.`
      : stop ? `No current vehicles have ${stopName} as their next stop.`
      : modeName ? `No ${modeName} positions are available in this snapshot. The feed may be temporarily empty or out of date.`
      : "No current vehicles are available in the selected area.";
    controls.filterStatus.hidden = false;
  }

  const source = snapshot.source === "demo" ? "DEMO · " : "";
  const updated = snapshot.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleTimeString() : "unknown";
  controls.status.textContent = `${source}${buses.length} of ${snapshot.count} vehicles shown · ${oldCount} old positions · snapshot ${updated}`;
  const unavailable = Object.entries(snapshot.feed_status || {}).filter(([, state]) =>
    state && typeof state === "object" && (state.positions !== "ok" || state.updates !== "ok")
  ).map(([name]) => name);
  const cached = Object.entries(snapshot.feed_status || {}).filter(([, state]) =>
    state && typeof state === "object" && state.positions_cached_count > 0
  ).map(([name]) => name);
  controls.feedStatus.hidden = unavailable.length === 0 && cached.length === 0;
  controls.feedStatus.textContent = [
    unavailable.length ? `Feeds unavailable: ${unavailable.join(", ")}. Check TfNSW API access or the pipeline log.` : "",
    cached.length ? `Showing last known positions for ${cached.join(", ")} in grey until fresh data arrives.` : ""
  ].filter(Boolean).join(" ");
}

function attachPlaceAutocomplete(input, datalist) {
  let requestId = 0;
  let timer;
  input.addEventListener("input", () => {
    delete input.dataset.areaToken;
    currentJourney = null;
    journeyRequestId++;
    journeyControls.results.replaceChildren();
    journeyStopLayer.clearLayers();
    const query = input.value.trim();
    clearTimeout(timer);
    const request = ++requestId;
    datalist.replaceChildren();
    if (query.length < 2) return;
    timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/places?q=${encodeURIComponent(query)}`, { cache: "no-store" });
        const payload = await response.json();
        if (request !== requestId) return;
        if (response.status === 202) {
          journeyControls.status.textContent = "Preparing the transit route index. Place suggestions will appear shortly…";
          timer = setTimeout(() => input.dispatchEvent(new Event("input")), 2000);
          return;
        }
        if (response.status === 404) {
          throw new Error("The map server is still running the old version. Restart it in the VS Code terminal, then refresh this page.");
        }
        if (!response.ok) throw new Error(payload.error || "Could not load places");
        for (const place of payload.places || []) {
          datalist.append(new Option(place.name, place.name));
        }
      } catch (error) {
        if (request === requestId) journeyControls.status.textContent = error.message;
      }
    }, 180);
  });
}

attachPlaceAutocomplete(journeyControls.from, journeyControls.fromOptions);
attachPlaceAutocomplete(journeyControls.to, journeyControls.toOptions);

function setPickingJourneyPoint(target) {
  pickingJourneyPoint = pickingJourneyPoint === target ? null : target;
  document.body.classList.toggle("map-picking", Boolean(pickingJourneyPoint));
  for (const button of journeyControls.pickButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.target === pickingJourneyPoint));
  }
  if (pickingJourneyPoint) journeyControls.status.textContent = `Click the map to choose your ${pickingJourneyPoint === "from" ? "starting" : "destination"} area.`;
}

function clearJourneySearch() {
  setPickingJourneyPoint(null);
  journeyControls.form.reset();
  for (const input of [journeyControls.from, journeyControls.to]) {
    delete input.dataset.areaToken;
    input.dispatchEvent(new Event("input"));
  }
  journeyControls.status.textContent = "Enter two places to see nearby direct routes.";
  journeyControls.from.focus();
}

journeyControls.clear.addEventListener("click", clearJourneySearch);
journeyControls.mode.addEventListener("change", () => {
  if (currentJourney) {
    currentJourney.mode = journeyControls.mode.value;
    runJourney(currentJourney);
  }
});

for (const button of journeyControls.pickButtons) {
  button.addEventListener("click", () => setPickingJourneyPoint(button.dataset.target));
}
map.on("click", event => {
  if (!pickingJourneyPoint) return;
  const input = journeyControls[pickingJourneyPoint];
  input.value = `Map point (${event.latlng.lat.toFixed(4)}, ${event.latlng.lng.toFixed(4)})`;
  input.dataset.areaToken = `@${event.latlng.lat.toFixed(6)},${event.latlng.lng.toFixed(6)}`;
  currentJourney = null;
  journeyRequestId++;
  journeyControls.results.replaceChildren();
  journeyStopLayer.clearLayers();
  setPickingJourneyPoint(pickingJourneyPoint);
  journeyControls.status.textContent = "Point chosen. Choose the other area, then search.";
});

function showJourneyStops(result) {
  journeyStopLayer.clearLayers();
  for (const [stop, label, colour] of [
    [result.origin, "Start", "#1c9a6a"],
    [result.destination, "Destination", "#c0603a"]
  ]) {
    L.circle([stop.latitude, stop.longitude], {
      radius: result.radius_metres, color: colour, weight: 1.5,
      fillColor: colour, fillOpacity: 0.08, interactive: false
    }).addTo(journeyStopLayer);
    L.circleMarker([stop.latitude, stop.longitude], {
      radius: 9, color: "#ffffff", weight: 3, fillColor: colour, fillOpacity: 1
    }).bindPopup(`<strong>${label} area</strong><br>${escapeHtml(stop.name)}<br>Transit stops within ${result.radius_metres} m`).addTo(journeyStopLayer);
  }
}

function renderJourney(result, focusStops = false) {
  journeyControls.results.replaceChildren();
  showJourneyStops(result);
  if (focusStops) {
    map.fitBounds(L.latLngBounds([
      [result.origin.latitude, result.origin.longitude],
      [result.destination.latitude, result.destination.longitude]
    ]).pad(0.25), { padding: [60, 60], maxZoom: 15 });
  }
  const routeCount = result.routes.length;
  const vehicleCount = result.routes.reduce((count, route) => count + route.buses.length, 0);
  const snapshotAge = Date.now() - Date.parse(result.snapshot_time || "");
  const liveNote = result.source === "demo" ? "Demo mode has no live arrivals."
    : result.source !== "live" ? "Live vehicle data is unavailable."
    : !Number.isFinite(snapshotAge) || snapshotAge > OLD_POSITION_MS ? "Live snapshot is old; no arrivals are shown."
    : vehicleCount ? `${vehicleCount} vehicle${vehicleCount === 1 ? "" : "s"} predicted within ${result.arrival_window_minutes} min.`
    : `No vehicle is currently predicted within ${result.arrival_window_minutes} min.`;
  journeyControls.status.textContent = routeCount
    ? `${routeCount} direct route${routeCount === 1 ? "" : "s"} across ${result.origin_stop_count} nearby start stops and ${result.destination_stop_count} destination stops · ${liveNote}`
    : `No direct route was found between these areas. Try another nearby point or a longer walk. ${liveNote}`;

  for (const route of result.routes) {
    const card = document.createElement("article");
    card.className = "journey-card";
    const heading = document.createElement("div");
    heading.className = "journey-route-heading";
    const number = document.createElement("span");
    number.className = "journey-route-number";
    const modeName = route.mode === "light_rail" ? "Light rail" : route.mode === "train" ? "Train" : route.mode === "metro" ? "Metro" : "Bus";
    number.textContent = `${modeName} ${route.route}`;
    const destination = document.createElement("span");
    destination.className = "journey-destination";
    destination.textContent = route.headsign || route.route_long_name || "Direct service";
    heading.append(number, destination);
    card.append(heading);
    const stopPair = document.createElement("p");
    stopPair.className = "journey-stop-pair";
    stopPair.textContent = `Board: ${route.boarding_stop.name} (${route.boarding_stop.distance_metres} m) · Get off: ${route.alighting_stop.name} (${route.alighting_stop.distance_metres} m)`;
    card.append(stopPair);
    if (route.route_long_name && route.route_long_name !== destination.textContent) {
      const detail = document.createElement("p");
      detail.className = "journey-route-detail";
      detail.textContent = route.route_long_name;
      card.append(detail);
    }
    if (route.buses.length) {
      const arrivals = document.createElement("div");
      arrivals.className = "journey-arrivals";
      for (const bus of route.buses) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "arrival-row";
        const vehicle = document.createElement("span");
        vehicle.textContent = `${modeName} ${bus.vehicle_id || bus.trip_id}${bus.headsign ? ` to ${bus.headsign}` : ""} · ${new Date(bus.eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} at ${bus.boarding_stop.name}`;
        const minutes = document.createElement("strong");
        minutes.textContent = bus.minutes === 0 ? "Due" : `${bus.minutes} min`;
        button.append(vehicle, minutes);
        button.addEventListener("click", () => {
          selectTransportMode(route.mode, false);
          updateFilters();
          controls.route.value = route.route;
          updateFilters();
          controls.stop.value = "";
          render();
          updateRouteShape();
          map.panTo([bus.latitude, bus.longitude]);
          vehicleMarkers.get(`${route.mode}:${bus.vehicle_id}`)?.marker.openPopup();
        });
        arrivals.append(button);
      }
      card.append(arrivals);
    } else {
      const noBus = document.createElement("p");
      noBus.className = "journey-no-bus";
      noBus.textContent = "No approaching vehicle with a fresh arrival prediction.";
      card.append(noBus);
    }
    journeyControls.results.append(card);
  }
}

async function runJourney(journey, focusStops = false) {
  const request = ++journeyRequestId;
  lastJourneyFetch = Date.now();
  journeyControls.status.textContent = "Searching direct routes and arrival predictions…";
  try {
    const params = new URLSearchParams({ from: journey.from, to: journey.to, mode: journey.mode });
    const response = await fetch(`/api/journey-area?${params}`, { cache: "no-store" });
    const payload = await response.json();
    if (request !== journeyRequestId) return;
    if (response.status === 202) {
      journeyControls.status.textContent = "Preparing the transit route index. Search will continue shortly…";
      setTimeout(() => { if (request === journeyRequestId) runJourney(journey, focusStops); }, 2000);
      return;
    }
    if (response.status === 404) {
      throw new Error("The map server is still running the old version. Restart it in the VS Code terminal, then refresh this page.");
    }
    if (!response.ok) throw new Error(payload.error || "Could not search direct routes");
    renderJourney(payload, focusStops);
  } catch (error) {
    if (request === journeyRequestId) journeyControls.status.textContent = error.message;
  }
}

function clearShape() {
  shapeRequest++;
  if (routeShape) map.removeLayer(routeShape);
  routeShape = null;
  routeShapeKey = null;
  pendingShapeKey = null;
}

async function loadTripShape(tripId) {
  if (tripShapeCache.has(tripId)) return tripShapeCache.get(tripId);
  const response = await fetch(`/api/route-shape?trip_id=${encodeURIComponent(tripId)}`);
  if (!response.ok) return null;
  const shape = await response.json();
  if (!Array.isArray(shape.coordinates) || shape.coordinates.length < 2) return null;
  tripShapeCache.set(tripId, shape);
  return shape;
}

async function updateRouteShape() {
  const route = controls.route.value;
  if (!route || !controls.showRoute.checked) {
    clearShape();
    controls.shapeStatus.textContent = route ? "Scheduled route path hidden." : "Choose a route to see scheduled trip paths.";
    return;
  }
  if (snapshot?.source === "demo") {
    clearShape();
    controls.shapeStatus.textContent = "Demo vehicles have sample trip IDs. Scheduled route paths are available with live data.";
    return;
  }
  const buses = snapshot.vehicles.filter(bus => String(bus.route) === route &&
    (controls.transport.value === "all" || bus.mode === controls.transport.value));
  const candidates = [...new Set(buses.map(bus => bus.trip_id).filter(Boolean))].sort();
  const requestKey = JSON.stringify([controls.transport.value, route, candidates]);
  if (routeShape && routeShapeKey === requestKey) return;
  if (pendingShapeKey === requestKey) return;
  clearShape();
  pendingShapeKey = requestKey;
  const request = shapeRequest;
  controls.shapeStatus.textContent = "Loading scheduled trip paths…";
  const uniqueShapes = new Map();
  const shapes = await Promise.all(candidates.map(tripId => loadTripShape(tripId).catch(() => null)));
  if (request !== shapeRequest) return;
  shapes.forEach((shape, index) => {
    if (!shape) return;
    const tripId = candidates[index];
    const destination = buses.find(bus => bus.trip_id === tripId)?.headsign || "";
    uniqueShapes.set(shape.shape_id || tripId, { shape, destination });
  });
  pendingShapeKey = null;
  if (!uniqueShapes.size) {
    controls.shapeStatus.textContent = "No scheduled path available for current vehicles on this route.";
    return;
  }
  routeShape = L.layerGroup().addTo(map);
  for (const { shape, destination } of uniqueShapes.values()) {
    const line = L.polyline(shape.coordinates, {
      color: ROUTE_PATH_COLOUR, weight: 4, opacity: 0.9
    }).addTo(routeShape);
    line.bindPopup(`Scheduled path for route ${escapeHtml(route)}${destination ? `<br>To ${escapeHtml(destination)}` : ""}`);
    line.bringToBack();
  }
  routeShapeKey = requestKey;
  const count = uniqueShapes.size;
  const destinations = [...new Set([...uniqueShapes.values()].map(item => item.destination).filter(Boolean))];
  const destinationText = destinations.length ? ` (${destinations.join(" / ")})` : "";
  controls.shapeStatus.textContent = `Purple ${count === 1 ? "line" : "lines"}: ${count} scheduled ${count === 1 ? "path" : "paths"} for current ${route} trips${destinationText}. Markers show reported positions, which can be away from a path.`;
}

let lastSnapshotText = null;

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const response = await fetch("/api/vehicles", { cache: "no-store" });
    const text = await response.text();
    if (response.ok && text === lastSnapshotText) {
      // Same pipeline snapshot: only position ages (and grey "old" markers) change.
      render();
      if (currentJourney && Date.now() - lastJourneyFetch > 20_000) runJourney(currentJourney);
      controls.error.hidden = true;
      return;
    }
    const payload = JSON.parse(text);
    if (!response.ok) throw new Error(payload.error || "Could not load transit data");
    lastSnapshotText = text;
    snapshot = payload;
    for (const stop of payload.stops || []) knownStops.set(String(stop.id), stop);
    updateFilters();
    render();
    updateRouteShape();
    if (currentJourney && Date.now() - lastJourneyFetch > 20_000) runJourney(currentJourney);
    controls.error.hidden = true;
  } catch (error) {
    controls.error.textContent = error.message;
    controls.error.hidden = false;
    if (!snapshot) controls.status.textContent = "Waiting for transit data…";
    else render();
  } finally {
    refreshInFlight = false;
  }
}

const INTERACTION_HANDLERS = ["dragging", "scrollWheelZoom", "doubleClickZoom", "touchZoom", "boxZoom", "keyboard"];
const calibrationHint = document.getElementById("calibration-hint");
let calibrating = false;
let calibrationOutline = null;

function setMapInteraction(enabled) {
  for (const name of INTERACTION_HANDLERS) {
    if (enabled) map[name].enable();
    else map[name].disable();
  }
  map.zoomControl.getContainer().hidden = !enabled;
}

// In projection mode the view is locked to map_area.json so it lines up with the physical model.
function setProjectionMode(enabled) {
  if (!enabled && calibrating) setCalibrating(false);
  projectionMode = enabled;
  document.body.classList.toggle("projection-mode", enabled);
  map.options.zoomSnap = enabled ? 0 : 0.25;
  setMapInteraction(!enabled);
  if (!enabled && panel.classList.contains("panel-moved")) {
    requestAnimationFrame(() => {
      const bounds = panel.getBoundingClientRect();
      setPanelPosition(bounds.left, bounds.top);
    });
  }
  setTimeout(() => {
    map.invalidateSize();
    fitMapArea();
  }, 100);
}

function setCalibrating(enabled, message = "") {
  calibrating = enabled;
  document.body.classList.toggle("calibrating", enabled);
  setMapInteraction(enabled);
  map.keyboard.disable();  // calibrationKey handles the arrow and zoom keys itself
  // Fine scroll steps so the projection can be matched to the model edge.
  map.options.wheelPxPerZoomLevel = enabled ? 600 : 60;
  if (calibrationOutline) map.removeLayer(calibrationOutline);
  calibrationOutline = enabled
    ? L.rectangle(areaBounds(), { color: "#ff3b30", weight: 2, fill: false, dashArray: "6 6", interactive: false }).addTo(map)
    : null;
  calibrationHint.hidden = !enabled && !message;
  calibrationHint.textContent = message || "Calibration: drag or use arrow keys to move (Shift = faster), scroll or +/- to zoom ([ ] = finer). Enter saves the projected area, Esc cancels.";
  if (message) setTimeout(() => { if (!calibrating) calibrationHint.hidden = true; }, 4000);
}

async function saveCalibration() {
  const view = map.getBounds();
  const area = { south: view.getSouth(), west: view.getWest(), north: view.getNorth(), east: view.getEast() };
  try {
    const response = await fetch("/api/map-area", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(area)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save the map area");
    mapArea = payload;
    setCalibrating(false, "Map area saved to map_area.json. The pipeline uses it from its next cycle.");
  } catch (error) {
    calibrationHint.textContent = error.message;
  }
}

function calibrationKey(event) {
  const step = event.shiftKey ? 40 : 4;
  const pans = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  const zooms = { "+": 0.05, "=": 0.05, "-": -0.05, "_": -0.05, "]": 0.01, "[": -0.01 };
  if (pans[event.key]) map.panBy(pans[event.key], { animate: false });
  else if (zooms[event.key]) map.setZoom(map.getZoom() + zooms[event.key], { animate: false });
  else if (event.key === "Enter") saveCalibration();
  else if (event.key === "Escape") { setCalibrating(false); fitMapArea(); }
  else return false;
  event.preventDefault();
  return true;
}

function toggleLabels() {
  labelsVisible = !labelsVisible;
  updateLabels();
}
map.on("zoomend", updateLabels);

for (const chip of transportChips) {
  chip.addEventListener("click", () => selectTransportMode(chip.dataset.transport));
}
controls.route.addEventListener("change", () => { updateFilters(); render(); updateRouteShape(); });
journeyControls.form.addEventListener("submit", event => {
  event.preventDefault();
  const from = journeyControls.from.dataset.areaToken || journeyControls.from.value.trim();
  const to = journeyControls.to.dataset.areaToken || journeyControls.to.value.trim();
  if (!from || !to) {
    journeyControls.status.textContent = "Enter two places or choose points on the map.";
    return;
  }
  if (from.toLowerCase() === to.toLowerCase()) {
    journeyControls.status.textContent = "Choose two different areas.";
    return;
  }
  currentJourney = { from, to, mode: journeyControls.mode.value };
  journeyControls.results.replaceChildren();
  journeyStopLayer.clearLayers();
  runJourney(currentJourney, true);
});
controls.stop.addEventListener("change", () => {
  const selectedStop = knownStops.get(controls.stop.value);
  if (selectedStop) map.panTo([selectedStop.latitude, selectedStop.longitude]);
  render();
});
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
  if (calibrating && calibrationKey(event)) return;
  const key = event.key.toLowerCase();
  if (key === "c" && projectionMode && !calibrating) setCalibrating(true);
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
selectTransportMode(controls.transport.value, false);
restorePanelPosition();
fitMapArea();
setProjectionMode(projectionMode);
fetch("/api/map-area", { cache: "no-store" })
  .then(response => response.ok ? response.json() : null)
  .then(area => { if (area) { mapArea = area; fitMapArea(); } })
  .catch(() => { /* keep the built-in area */ });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);
