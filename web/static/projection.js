const BOUNDS = [[-33.90, 151.18], [-33.84, 151.23]];
const REFRESH_MS = 5_000;

const colours = {
  early: "#4da3ff",
  on_time: "#39d98a",
  minor_delay: "#ffb020",
  major_delay: "#ff4d4f",
  no_delay_data: "#a7adb4"
};

const map = L.map("map", {
  zoomControl: false,
  attributionControl: true,
  preferCanvas: true,
  maxBounds: BOUNDS,
  maxBoundsViscosity: 1
});

map.fitBounds(BOUNDS, { padding: [4, 4] });

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const markerLayer = L.layerGroup().addTo(map);
let labelsVisible = true;
let projectionMode = new URLSearchParams(window.location.search).get("projection") === "1";

function delayText(bus) {
  const raw = bus.delay_seconds;

  if (raw === null || raw === undefined || raw === "") {
    return "No delay data";
  }

  const delay = Number(raw);

  if (!Number.isFinite(delay)) {
    return "No delay data";
  }

  const totalSeconds = Math.round(Math.abs(delay));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (totalSeconds === 0) {
    return "On time";
  }

  const direction = delay < 0 ? "early" : "late";
  return `${minutes} min ${seconds} sec ${direction}`;
}

function renderBuses(payload) {
  markerLayer.clearLayers();
  for (const bus of payload.buses) {
    const colour = colours[bus.delay_status] || colours.no_delay_data;
    const marker = L.circleMarker([bus.latitude, bus.longitude], {
      radius: 9,
      color: "#ffffff",
      weight: 2,
      fillColor: colour,
      fillOpacity: 0.96
    });

    marker.bindPopup(
      `<strong>Route ${bus.route}</strong><br>` +
      `${delayText(bus)}<br>` +
      `Vehicle ${bus.vehicle_id || "unknown"}`
    );
    marker.bindTooltip(String(bus.route), {
      permanent: labelsVisible,
      direction: "top",
      offset: [0, -8],
      className: "bus-label"
    });
    marker.addTo(markerLayer);
  }

  const source = payload.source === "demo" ? "DEMO DATA · " : "";
  const time = payload.snapshot_time ? new Date(payload.snapshot_time).toLocaleString() : "unknown time";
  document.getElementById("status").textContent = `${source}${payload.count} buses · Updated ${time}`;
}

async function refresh() {
  const errorBox = document.getElementById("error");
  try {
    const response = await fetch("/api/buses", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load bus data");
    renderBuses(payload);
    errorBox.hidden = true;
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    document.getElementById("status").textContent = "Waiting for bus data…";
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

document.addEventListener("keydown", event => {
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

setProjectionMode(projectionMode);
refresh();
setInterval(refresh, REFRESH_MS);
