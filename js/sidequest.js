// Side quest selection: hop-off stop, walking directions, rejoin buses.
// Must be loaded AFTER map.js and landmarks.js.

// Free OSM pedestrian router, no API key. Falls back to a straight line if it fails.
const VALHALLA = "https://valhalla1.openstreetmap.de/route";
const VISIT_MINUTES = 60;   // default time spent at the landmark

const questLayer = L.layerGroup().addTo(map);
const panel = document.getElementById("quest-panel");

// -----------------------
// Geometry helpers
// -----------------------

function haversineKm(lat1, lon1, lat2, lon2) {
  const p = Math.PI / 180;
  const a = Math.sin((lat2 - lat1) * p / 2) ** 2 +
    Math.cos(lat1 * p) * Math.cos(lat2 * p) * Math.sin((lon2 - lon1) * p / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}

// Valhalla returns polyline6 (lat, lon order).
function decodePolyline6(str) {
  let index = 0, lat = 0, lon = 0;
  const out = [];
  while (index < str.length) {
    for (const which of ["lat", "lon"]) {
      let result = 0, shift = 0, b;
      do {
        b = str.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
      if (which === "lat") lat += delta; else lon += delta;
    }
    out.push([lat / 1e6, lon / 1e6]);
  }
  return out;
}

// -----------------------
// Stop selection
// -----------------------

function nearestStop(route, lat, lon, predicate) {
  let best = null;
  route.stops.forEach((stop, i) => {
    if (stop.lat == null || stop.lon == null) return;
    if (predicate && !predicate(stop)) return;
    const d = haversineKm(lat, lon, stop.lat, stop.lon);
    if (!best || d < best.km) best = { stop, index: i, km: d };
  });
  return best;
}

function rejoinStop(route, hopOffIndex) {
  // Same stop if you can board there, else the next one along that allows it.
  for (let i = hopOffIndex; i < route.stops.length - 1; i++) {
    if (route.stops[i].allow_boarding !== false) return { stop: route.stops[i], index: i };
  }
  return { stop: route.stops[hopOffIndex], index: hopOffIndex };
}

// -----------------------
// Walking directions
// -----------------------

async function walkingRoute(from, to) {
  // from/to are {lat, lon}. Returns { points: [[lat,lon],...], km, minutes, real }.
  try {
    const body = {
      locations: [{ lat: from.lat, lon: from.lon }, { lat: to.lat, lon: to.lon }],
      costing: "pedestrian",
      units: "kilometers"
    };
    const res = await fetch(`${VALHALLA}?json=${encodeURIComponent(JSON.stringify(body))}`);
    if (!res.ok) throw new Error(`Valhalla ${res.status}`);
    const data = await res.json();
    const leg = data.trip.legs[0];
    return {
      points: decodePolyline6(leg.shape),
      km: data.trip.summary.length,
      minutes: Math.round(data.trip.summary.time / 60),
      real: true
    };
  } catch (error) {
    console.warn("Walking router failed, using straight line:", error);
    const km = haversineKm(from.lat, from.lon, to.lat, to.lon);
    return { points: [[from.lat, from.lon], [to.lat, to.lon]], km, minutes: Math.round(km / 5 * 60), real: false };
  }
}

// -----------------------
// Rejoin buses
// -----------------------

async function rejoinOptions(route, stopIndex, dwellMinutes) {
  const url = `${API}/api/rejoin?trip_uid=${encodeURIComponent(route.trip_uid)}` +
    `&stop_index=${stopIndex}&dwell=${dwellMinutes}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`rejoin ${res.status}`);
  return res.json();
}

// -----------------------
// Panel
// -----------------------

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function renderPanel(poi, hopOff, walkThere, rejoin, walkBack, buses, image) {
  panel.replaceChildren();
  panel.classList.add("open");

  const close = el("button", "×", "quest-close");
  close.setAttribute("aria-label", "Close quest");
  close.addEventListener("click", clearSideQuest);
  panel.appendChild(close);

  // Banner: the landmark's picture stays on screen for the whole quest
  if (image && image.url) {
    const banner = el("div", null, "quest-banner");
    const img = document.createElement("img");
    img.src = image.url;
    img.alt = poi.name;
    img.addEventListener("error", () => banner.remove());
    banner.appendChild(img);
    panel.appendChild(banner);
  }

  panel.appendChild(el("div", "Quest accepted", "quest-stamp"));
  panel.appendChild(el("h2", poi.name));

  const steps = el("ol", null, "quest-steps");
  steps.appendChild(el("li", `Hop off at ${hopOff.stop.name} — the bus lands there at ${fmtTime(hopOff.stop.arrival)}`));
  steps.appendChild(el("li", `Walk ${walkThere.km.toFixed(1)} km, about ${walkThere.minutes} min` +
    (walkThere.real ? "" : " (estimate)")));
  steps.appendChild(el("li", `Explore ${poi.name} for about ${VISIT_MINUTES} min`));
  steps.appendChild(el("li", rejoin.index === hopOff.index
    ? `Walk back to ${rejoin.stop.name} (${walkBack.minutes} min)`
    : `Walk on to ${rejoin.stop.name} (${walkBack.km.toFixed(1)} km, ${walkBack.minutes} min) — no boarding at ${hopOff.stop.name}`));
  panel.appendChild(steps);

  panel.appendChild(el("h3", "Catch the next bus"));
  if (!buses || buses.length === 0) {
    panel.appendChild(el("p", "No later buses today from this stop. Try a shorter visit or an earlier landmark."));
    return;
  }
  const list = el("ul", null, "quest-buses");
  buses.slice(0, 4).forEach((b) => {
    const seats = b.seats_available === 0 ? " · sold out" : b.seats_available != null ? ` · ${b.seats_available} seats` : "";
    list.appendChild(el("li", `${fmtTime(b.departure)} → ${fmtTime(b.arrival)}  ${fmtPrice(b.price_adult_pence)}${seats}`));
  });
  panel.appendChild(list);
}

// -----------------------
// Main entry point (called from the landmark popup button)
// -----------------------

async function selectSideQuest(poi) {
  if (!currentRoute) {
    alert("Find a route first.");
    return;
  }
  questLayer.clearLayers();
  map.closePopup();

  const hopOff = nearestStop(currentRoute, poi.lat, poi.lon, (s) => s.allow_drop_off !== false);
  if (!hopOff) {
    alert("No stop on this route allows getting off.");
    return;
  }
  const rejoin = rejoinStop(currentRoute, hopOff.index);

  // Highlight the hop-off stop
  L.circleMarker([hopOff.stop.lat, hopOff.stop.lon], {
    radius: 12, color: "#d9534f", weight: 3, fillColor: "#d9534f", fillOpacity: 0.35,
    className: "hop-off-pulse"
  }).bindTooltip(`Get off here: ${escapeHtml(hopOff.stop.name)}`, { permanent: true, direction: "top" })
    .addTo(questLayer);

  if (rejoin.index !== hopOff.index) {
    L.circleMarker([rejoin.stop.lat, rejoin.stop.lon], {
      radius: 10, color: "#E2A72E", weight: 3, fillColor: "#E2A72E", fillOpacity: 0.4
    }).bindTooltip(`Rejoin here: ${escapeHtml(rejoin.stop.name)}`, { permanent: true, direction: "top" })
      .addTo(questLayer);
  }

  panel.replaceChildren(el("p", "Charting the path…", "quest-loading"));
  panel.classList.add("open");

  // Image: /api/pois may already include one; otherwise reuse the popup's Wikipedia lookup
  const imagePromise = poi.image
    ? Promise.resolve({ url: poi.image })
    : (typeof getPoiImage === "function" ? getPoiImage(poi.name).catch(() => null) : Promise.resolve(null));

  const [walkThere, walkBack, image] = await Promise.all([
    walkingRoute({ lat: hopOff.stop.lat, lon: hopOff.stop.lon }, { lat: poi.lat, lon: poi.lon }),
    walkingRoute({ lat: poi.lat, lon: poi.lon }, { lat: rejoin.stop.lat, lon: rejoin.stop.lon }),
    imagePromise
  ]);

  const there = L.polyline(walkThere.points, { color: "#C8372D", weight: 5, dashArray: "10 8" }).addTo(questLayer);
  L.polyline(walkBack.points, { color: "#C8372D", weight: 5, dashArray: "2 9", opacity: 0.75 }).addTo(questLayer);
  map.fitBounds(there.getBounds().extend([poi.lat, poi.lon]).extend([rejoin.stop.lat, rejoin.stop.lon]),
    { padding: [60, 60], maxZoom: 15 });

  let buses = [];
  try {
    const dwell = walkThere.minutes + VISIT_MINUTES + walkBack.minutes;
    buses = await rejoinOptions(currentRoute, rejoin.index, dwell);
  } catch (error) {
    console.error("Could not load rejoin buses:", error);
  }

  renderPanel(poi, hopOff, walkThere, rejoin, walkBack, buses, image);
}

function clearSideQuest() {
  questLayer.clearLayers();
  panel.classList.remove("open");
  panel.replaceChildren();
}
