// Must be loaded AFTER map.js (it uses the `map` variable).

// -----------------------
// Landmark API calls
// -----------------------

const API = "http://138.251.29.106:8000";

const infoCache = new Map(); // poiName -> summary text

async function getPois(start, end) {
  const url =
    `${API}/api/pois?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;

  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);

  return res.json(); // [{ name, lon, lat }, ...]
}

async function getPoiInfo(poiName) {
  if (infoCache.has(poiName)) return infoCache.get(poiName);

  const url = `${API}/api/poi-info?poiName=${encodeURIComponent(poiName)}`;

  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error ${res.status}`);

  const { summary } = await res.json();
  infoCache.set(poiName, summary);
  return summary;
}


// -----------------------
// Landmark markers
// -----------------------

// Holds all landmark markers so they can be cleared for the next route
const landmarkLayer = L.layerGroup().addTo(map);

// The text comes from a language model, so escape it before putting it in HTML
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function addLandmark(poi) {
  // Skip anything with unusable coordinates
  if (typeof poi.lat !== "number" || typeof poi.lon !== "number") return;

  // API returns lon/lat, Leaflet wants [lat, lon]
  const marker = L.marker([poi.lat, poi.lon]).addTo(landmarkLayer);
  const title = escapeHtml(poi.name);

  // Hover: name only
  marker.bindTooltip(title);

  // Click: popup, with the summary loaded the first time it opens
  marker.bindPopup(`<strong>${title}</strong><p>Loading…</p>`);

  marker.on("popupopen", async () => {
    try {
      const summary = await getPoiInfo(poi.name);
      marker.setPopupContent(`<strong>${title}</strong><p>${escapeHtml(summary)}</p>`);
    } catch (error) {
      console.error(error);
      marker.setPopupContent(`<strong>${title}</strong><p>Couldn't load info.</p>`);
    }
  });
}

// Called from map.js after a route is drawn
async function showLandmarks(originText, destinationText) {
  landmarkLayer.clearLayers();

  try {
    const pois = await getPois(originText, destinationText);
    pois.forEach(addLandmark);
  } catch (error) {
    // Landmarks are a bonus, so don't break the route if they fail
    console.error("Could not load landmarks:", error);
  }
}