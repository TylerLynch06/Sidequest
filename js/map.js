// -----------------------
// Create map
// -----------------------

const startPosition = [56.34, -2.80];

const map = L.map("map").setView(startPosition, 10);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);


// -----------------------
// Geocoding
// -----------------------

async function geocode(placeName) {

    const url =
        "https://nominatim.openstreetmap.org/search" +
        "?format=json" +
        "&limit=1" +
        "&q=" + encodeURIComponent(placeName);

    const response = await fetch(url);
    const results = await response.json();

    if (results.length === 0) {
        throw new Error("Location not found");
    }

    return [
        parseFloat(results[0].lat),
        parseFloat(results[0].lon)
    ];
}


// -----------------------
// Route button
// -----------------------

document
    .getElementById("route-button")
    .addEventListener("click", async function () {

        const originText =
            document.getElementById("origin").value;

        const destinationText =
            document.getElementById("destination").value;

        try {

            // Find coordinates
            const origin = await geocode(originText);
            const destination = await geocode(destinationText);

            // Add markers
            L.marker(origin)
                .addTo(map)
                .bindPopup("Origin");

            L.marker(destination)
                .addTo(map)
                .bindPopup("Destination");

            // TODO: ADD JOE's CODE
            const routePoints = [
                [56.340, -2.800],
                [56.345, -2.820],
                [56.350, -2.835],
                [56.355, -2.850],
                [56.360, -2.870],
                [56.370, -2.890]
            ];

            const routeLine = L.polyline(routePoints, {
                color: "#4F917A",
                weight: 5
            }).addTo(map);

            // Fit map around route
            map.fitBounds(routeLine.getBounds(), {
                padding: [50, 50]
            });

        } catch (error) {

            alert("Could not find one of those locations.");

            console.error(error);
        }

    });