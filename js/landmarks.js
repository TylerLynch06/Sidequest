// Must be loaded AFTER map.js (it uses the `map` variable).

// -----------------------
// Landmark API calls
// -----------------------

const API = "http://138.251.29.106:8000";

// Added to the Wikipedia search so "Law" finds Dundee Law, not a legal article.
// Set to "" to search by the bare name.
const IMAGE_SEARCH_HINT = "Scotland";

const poiCache = new Map();   // "start|end" -> pois array
const infoCache = new Map();  // poiName -> summary text
const imageCache = new Map(); // poiName -> { url, page } or null (misses are cached too)

async function getPois(start, end) {
  const key = `${start.toLowerCase()}|${end.toLowerCase()}`;
  if (poiCache.has(key)) return poiCache.get(key);

  const url =
    `${API}/api/pois?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;

  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);

  const pois = await res.json(); // [{ name, lon, lat }, ...]
  if (pois.length) poiCache.set(key, pois);   // an empty result isn't cached so a retry can succeed
  return pois;
}

// Scouting indicator shown while landmarks load
const scoutToast = document.getElementById("scout-toast");

function setScouting(on, message) {
  if (!scoutToast) return;
  if (message) scoutToast.querySelector(".scout-text").textContent = message;
  scoutToast.classList.toggle("open", on);
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

async function getPoiImage(poiName) {
  if (imageCache.has(poiName)) return imageCache.get(poiName);

  const queries = [`${poiName} ${IMAGE_SEARCH_HINT}`.trim(), poiName];
  let result = null;

  for (const q of queries) {
    const url =
      "https://en.wikipedia.org/w/api.php?action=query&format=json&origin=*" +
      "&generator=search&gsrlimit=5&prop=pageimages&piprop=thumbnail&pithumbsize=400" +
      `&gsrsearch=${encodeURIComponent(q)}`;
    try {
      const res = await fetch(url);
      if (!res.ok) continue;
      const data = await res.json();
      const pages = Object.values((data.query && data.query.pages) || {})
        .sort((a, b) => a.index - b.index);        // keep search ranking order
      const hit = pages.find((p) => p.thumbnail);  // first result that has a picture
      if (hit) {
        result = {
          url: hit.thumbnail.source,
          page: `https://en.wikipedia.org/?curid=${hit.pageid}`,
        };
        break;
      }
    } catch (error) {
      console.error("Image lookup failed:", error);
    }
  }

  console.log("image for", poiName, "->", result);   // remove once it works
  imageCache.set(poiName, result);
  return result;
}


// -----------------------
// Landmark markers
// -----------------------

// Holds all landmark markers so they can be cleared for the next route
const landmarkLayer = L.layerGroup().addTo(map);

// X marks the spot: a hand-drawn red cross instead of the default pin
const crossIcon = L.divIcon({
  className: "x-marker",
  iconSize: [30, 30],
  iconAnchor: [15, 15],
  popupAnchor: [0, -14],
  html: `<svg viewBox="0 0 30 30" width="30" height="30">
    <path d="M6 5 L24 25 M24 5 L6 25" stroke="#3A2A1A" stroke-width="9" stroke-linecap="round"/>
    <path d="M6 5 L24 25 M24 5 L6 25" stroke="#C8372D" stroke-width="5" stroke-linecap="round"/>
  </svg>`
});

// The text comes from a language model, so escape it before putting it in HTML
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Builds the popup from DOM nodes (not an HTML string), so model text and
// image URLs are never parsed as markup.
function buildPopup(name, summary, image, popup, poi) {
  const box = document.createElement("div");
  box.style.maxWidth = "240px";

  if (image) {
    const img = document.createElement("img");
    img.src = image.url;
    img.alt = name;
    img.style.cssText = "width:100%;border-radius:6px;margin-bottom:6px;display:block";
    img.addEventListener("load", () => popup && popup.update()); // re-fit popup once the image has loaded
    img.addEventListener("error", () => img.remove());
    box.appendChild(img);
  }

  const title = document.createElement("strong");
  title.textContent = name;
  box.appendChild(title);

  const text = document.createElement("p");
  text.textContent = summary;
  box.appendChild(text);

  if (poi) {
    const btn = document.createElement("button");
    btn.textContent = "Accept this quest";
    btn.className = "quest-button";
    btn.addEventListener("click", () => selectSideQuest(poi));
    box.appendChild(btn);
  }

  if (image) {
    const credit = document.createElement("a");
    credit.href = image.page;
    credit.target = "_blank";
    credit.rel = "noopener";
    credit.textContent = "Image: Wikipedia";
    credit.style.fontSize = "11px";
    box.appendChild(credit);
  }

  return box;
}

function addLandmark(poi) {
  // Skip anything with unusable coordinates
  if (typeof poi.lat !== "number" || typeof poi.lon !== "number") return;

  // API returns lon/lat, Leaflet wants [lat, lon]
  const marker = L.marker([poi.lat, poi.lon], { icon: crossIcon }).addTo(landmarkLayer);

  // Hover: name only
  marker.bindTooltip(escapeHtml(poi.name));

  // Click: popup, filled in the first time it opens
  marker.bindPopup(buildPopup(poi.name, "Loading…", null, null, poi), { maxWidth: 260 });

  let loaded = false;
  marker.on("popupopen", async () => {
    if (loaded) return;
    loaded = true;

    // Summary and image load in parallel; either one can fail without breaking the other
    const [summaryResult, imageResult] = await Promise.allSettled([
      getPoiInfo(poi.name),
      getPoiImage(poi.name),
    ]);

    if (summaryResult.status === "rejected") console.error(summaryResult.reason);

    const summary = summaryResult.status === "fulfilled" ? summaryResult.value : "Couldn't load info.";
    const image = imageResult.status === "fulfilled" ? imageResult.value : null;

    marker.setPopupContent(buildPopup(poi.name, summary, image, marker.getPopup(), poi));
  });
}

// Called from map.js after a route is drawn
async function showLandmarks(originText, destinationText) {
  landmarkLayer.clearLayers();
  setScouting(true, "Scouting for landmarks…");

  try {
    const pois = await getPois(originText, destinationText);
    pois.forEach(addLandmark);
    if (pois.length === 0) {
      setScouting(true, "No landmarks found along this way.");
      setTimeout(() => setScouting(false), 2500);
      return;
    }
  } catch (error) {
    // Landmarks are a bonus, so don't break the route if they fail
    console.error("Could not load landmarks:", error);
    setScouting(true, "The scouts got lost. Try again.");
    setTimeout(() => setScouting(false), 3000);
    return;
  }
  setScouting(false);
}