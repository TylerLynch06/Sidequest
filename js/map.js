// -----------------------
// Create map
// -----------------------

const startPosition = [56.34, -2.80];

// The chart covers the UK and Ireland only: you can't drag or zoom out beyond it
const UK_BOUNDS = L.latLngBounds([49.6, -11.0], [61.2, 2.5]);

const map = L.map("map", {
  maxBounds: UK_BOUNDS,
  maxBoundsViscosity: 1.0,   // hard edge, no rubber-banding past it
  minZoom: 5
}).setView(startPosition, 10);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

// Everything belonging to the current route lives in this group so a new
// search can wipe it in one call.
const routeLayer = L.layerGroup().addTo(map);

// Pennant flags for the start and end of the voyage
function flagIcon(colour, label) {
  return L.divIcon({
    className: "flag-marker",
    iconSize: [34, 40],
    iconAnchor: [4, 40],
    popupAnchor: [10, -36],
    html: `<svg viewBox="0 0 34 40" width="34" height="40" aria-label="${label}">
      <line x1="4" y1="2" x2="4" y2="40" stroke="#3A2A1A" stroke-width="3" stroke-linecap="round"/>
      <path d="M6 3 L32 10 L6 17 Z" fill="${colour}" stroke="#3A2A1A" stroke-width="2" stroke-linejoin="round"/>
    </svg>`
  });
}
const originFlag = flagIcon("#3FA07A", "Start");
const destinationFlag = flagIcon("#E2A72E", "Destination");

// The route currently on screen, used by sidequest.js
let currentRoute = null;


// -----------------------
// Route API (server.py)
// -----------------------

// `API` and `escapeHtml` are declared in landmarks.js, which loads after this
// file. That is fine: they are only used when the button is clicked, by which
// time both scripts have run.

async function getRoute(start, end) {
  const url =
    `${API}/api/route?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;

  const res = await fetch(url);
  if (!res.ok) {
    // server.py returns {"detail": "..."} for 404/502 — surface it to the user
    let detail = `API error ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json(); // { origin, destination, stops, geometry, bbox, departure, ... }
}

// Ember times are UTC ("2026-10-03T12:12:00+00:00"); show them in local time.
function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function fmtPrice(pence) {
  return pence == null ? "" : `£${(pence / 100).toFixed(2)}`;
}


// -----------------------
// Drawing
// -----------------------

function drawRoute(route) {
  routeLayer.clearLayers();

  // GeoJSON is [lon, lat]; Leaflet wants [lat, lon]
  const linePoints = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);

  // Ink underlay then jade on top: reads as a drawn trail rather than a GPS track
  L.polyline(linePoints, { color: "#3A2A1A", weight: 9, opacity: 0.9, lineJoin: "round" }).addTo(routeLayer);
  const routeLine = L.polyline(linePoints, {
    color: "#3FA07A",
    weight: 5,
    lineJoin: "round"
  }).addTo(routeLayer);

  // One marker per stop. Ends get the default pin; intermediate stops get a
  // small circle so landmarks (plain pins, from landmarks.js) stay distinct.
  route.stops.forEach((stop) => {
    if (stop.lat == null || stop.lon == null) return;

    const isEnd = stop.role === "origin" || stop.role === "destination";
    const marker = isEnd
      ? L.marker([stop.lat, stop.lon], { icon: stop.role === "origin" ? originFlag : destinationFlag })
      : L.circleMarker([stop.lat, stop.lon], {
          radius: 6,
          color: "#252A31",
          weight: 2,
          fillColor: "#ffffff",
          fillOpacity: 1
        });

    const lines = [
      `<strong>${escapeHtml(stop.name)}</strong>`,
      stop.detailed_name && stop.detailed_name !== stop.name ? escapeHtml(stop.detailed_name) : null,
      stop.arrival ? `Bus here at ${fmtTime(stop.arrival)}` : null,
      stop.allow_drop_off === false ? "No alighting here" : null,
      stop.allow_boarding === false ? "No boarding here" : null
    ].filter(Boolean);

    marker.bindPopup(lines.join("<br>"));
    marker.bindTooltip(escapeHtml(stop.name));
    marker.addTo(routeLayer);
  });

  // Fit map around route
  map.fitBounds(routeLine.getBounds(), { padding: [50, 50] });

  return routeLine;
}


// -----------------------
// Route button
// -----------------------

const routeButton = document.getElementById("route-button");

routeButton.addEventListener("click", async function () {

  const originText = document.getElementById("origin").value.trim();
  const destinationText = document.getElementById("destination").value.trim();

  if (!originText || !destinationText) {
    alert("Enter both an origin and a destination.");
    return;
  }

  routeButton.disabled = true;
  routeButton.textContent = "Finding route…";

  try {

    const route = await getRoute(originText, destinationText);
    currentRoute = route;
    if (typeof clearSideQuest === "function") clearSideQuest();
    drawRoute(route);

    console.log(
      `Route ${route.route_number || ""} ${route.origin.name} → ${route.destination.name}: ` +
      `${fmtTime(route.departure)}–${fmtTime(route.arrival)}, ${fmtPrice(route.price_adult_pence)}, ` +
      `${route.stops.length} stops (trip ${route.trip_uid})`
    );

    // Get landmarks near the route and plot them (see landmarks.js)
    showLandmarks(originText, destinationText);

  } catch (error) {

    alert(error.message || "Could not find a route between those places.");
    console.error(error);

  } finally {

    routeButton.disabled = false;
    routeButton.textContent = "Find sidequests";

  }

});
