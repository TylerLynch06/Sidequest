// Coordinates for the initial centre of the map
const startPosition = [56.34, -2.80];

// Create the Leaflet map
const map = L.map("map").setView(startPosition, 13);

// Add an OpenStreetMap tile layer
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);