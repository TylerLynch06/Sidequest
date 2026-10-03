"""
Side Quest API.

Run from the project root (the folder that contains routing/):
    source .venv/bin/activate
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload

Endpoints
    GET /api/places     ?q=dun[&origin=ID]            typeahead over Ember stops
    GET /api/route      ?start=..&end=..[&date=&journey=]   route line + stops (no LLM)
    GET /api/scenic     ?start=..&end=..[&date=&journey=]   route + validated landmarks
    GET /api/rejoin     ?trip_uid=..&stop_index=..[&dwell=60]   buses onward after a stop-off
    GET /api/pois       ?start=..&end=..               LLM-only POI list (unvalidated)
    GET /api/poi-info   ?poiName=..                    ~50 word summary of a place
"""

import json
import math
import os
import pathlib

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

from routing import planner
from routing.ember_client import EmberApiError, EmberClient
from concurrent.futures import ThreadPoolExecutor

import requests

# ------------------------------------------------------------------ setup

load_dotenv(pathlib.Path(__file__).parent / ".env")

HEADERS = {"User-Agent": f"side-quest-hackathon/0.1 ({os.environ["EMAIL"]})"}
MODEL = "qwen/qwen3.8-27b:nitro"

llm = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)
ember = EmberClient()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a hackathon, tighten later
    allow_methods=["*"],
    allow_headers=["*"],
)

cache = {}        # LLM results: ("pois", start, end) / ("info", name) / ("scenic", trip_uid)
route_cache = {}  # trip_uid -> route doc, needed by /api/rejoin
_loc_index = None  # Ember location index, built once (it is a big call)


# ---------------------------------------------------------------- helpers

MAX_DEVIATION_KM = 16.0   # how far a landmark may be from the route (16 km is about 10 miles)

def side_points(pts, cum, s, off_km):
    """Two points off_km to the left and right of the route at s km along it."""
    p, q = point_at(pts, cum, max(0.0, s - 1)), point_at(pts, cum, s + 1)
    k = math.cos(math.radians(p[1]))
    dx, dy = (q[0] - p[0]) * 111.32 * k, (q[1] - p[1]) * 110.57
    length = math.hypot(dx, dy)
    if length == 0:
        return []
    px, py = -dy / length, dx / length            # unit vector pointing left of travel
    c = point_at(pts, cum, s)
    out = []
    for sign in (1, -1):
        out.append((c[0] + sign * off_km * px / (111.32 * k),
                    c[1] + sign * off_km * py / 110.57))
    return out

def loc_index():
    global _loc_index
    if _loc_index is None:
        _loc_index = planner.build_location_index(ember)
    return _loc_index


def ember_call(fn, *args, **kwargs):
    """Run a routing call and translate its errors into HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:            # no matching stop / no journeys / bad index
        raise HTTPException(404, str(e))
    except EmberApiError as e:
        raise HTTPException(502, f"Ember API: {e}")

def wiki_near(point):
    """Wikipedia articles within 10 km of (lon, lat), with coordinates and a thumbnail if any."""
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "geosearch",
            "ggscoord": f"{point[1]}|{point[0]}", "ggsradius": 10000, "ggslimit": 50,
            "prop": "coordinates|description|pageimages", "colimit": 50,
            "piprop": "thumbnail", "pithumbsize": 400,
        }, headers=HEADERS, timeout=8)
        r.raise_for_status()
        return list((r.json().get("query") or {}).get("pages", {}).values())
    except requests.RequestException:
        return []


def plan(start, end, date=None, journey=0):
    """Two place names -> full route doc (same shape as planner.plan_route), cached by trip_uid."""
    origin = ember_call(planner.search_place, ember, start)
    destination = ember_call(planner.search_place, ember, end)
    journeys = ember_call(planner.find_journeys, ember, origin["id"], destination["id"], date)
    if not journeys:
        raise HTTPException(
            404, f"No direct Ember journeys from {origin['name']} to {destination['name']} in that window")
    if journey >= len(journeys):
        raise HTTPException(404, f"journey {journey} out of range (found {len(journeys)})")

    chosen = journeys[journey]
    sel = ember_call(planner.build_route, ember, chosen, loc_index())
    doc = {
        "origin": planner._place_summary(origin),
        "destination": planner._place_summary(destination),
        "selected_journey": sel,
    }
    if sel["trip_uid"]:
        route_cache[sel["trip_uid"]] = doc
    return doc


def route_payload(doc):
    sel = doc["selected_journey"]
    return {
        "origin": doc["origin"],
        "destination": doc["destination"],
        "trip_uid": sel["trip_uid"],
        "departure": sel["departure"],
        "arrival": sel["arrival"],
        "route_number": sel["route_number"],
        "price_adult_pence": sel["price_adult_pence"],
        "stops": sel["stops"],               # ordered, each with lat/lon
        "geometry": sel["geometry"],         # GeoJSON LineString, [lon, lat]
        "geometry_source": sel["geometry_source"],
        "bbox": sel["bbox"],                 # [minLon, minLat, maxLon, maxLat]
    }


def parse_json_reply(text):
    """Strip code fences the model sometimes adds, then parse."""
    text = text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, "Model did not return valid JSON")


def km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km."""
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))

def geocode(place):
    """Place name -> (lon, lat) via Nominatim, cached."""
    key = ("geo", place.lower())
    if key not in cache:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": place, "format": "json", "limit": 1},
                         headers=HEADERS, timeout=10)
        r.raise_for_status()
        hits = r.json()
        if not hits:
            raise HTTPException(404, f"Could not find '{place}'")
        cache[key] = (float(hits[0]["lon"]), float(hits[0]["lat"]))
    return cache[key]


def km_to_segment(p, a, b):
    """Distance in km from point p to the straight line a-b (all as (lon, lat))."""
    k = math.cos(math.radians(a[1]))
    def xy(q):
        return ((q[0] - a[0]) * 111.32 * k, (q[1] - a[1]) * 110.57)
    px, py = xy(p)
    bx, by = xy(b)
    length2 = bx * bx + by * by
    t = 0 if length2 == 0 else max(0, min(1, (px * bx + py * by) / length2))
    return math.hypot(px - t * bx, py - t * by)

def nearest(lon, lat, points):
    """(distance_km, index) of the closest [lon, lat] point."""
    return min((km(lon, lat, x, y), i) for i, (x, y) in enumerate(points))


def route_line(start, end, a, b):
    """Real route geometry from Ember if both places are Ember stops, else a straight line."""
    try:
        coords = plan(start, end)["selected_journey"]["geometry"]["coordinates"]
        if len(coords) >= 2:
            return [tuple(c) for c in coords]
    except Exception:
        pass
    return [a, b]


def thin(line, max_pts=250):
    """Keep the line to a manageable number of points."""
    step = max(1, len(line) // max_pts)
    out = line[::step]
    if out[-1] != line[-1]:
        out.append(line[-1])
    return out


def cumulative(pts):
    cum = [0.0]
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + km(p[0], p[1], q[0], q[1]))
    return cum


def point_at(pts, cum, s):
    """The (lon, lat) point s km along the line."""
    for i in range(len(pts) - 1):
        if cum[i + 1] >= s:
            seg = cum[i + 1] - cum[i]
            t = 0 if seg == 0 else (s - cum[i]) / seg
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
    return pts[-1]


def locate(p, pts, cum):
    """(distance_km from the line, km along the line) for point p."""
    best = (1e9, 0.0)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        k = math.cos(math.radians(a[1]))
        px, py = (p[0] - a[0]) * 111.32 * k, (p[1] - a[1]) * 110.57
        bx, by = (b[0] - a[0]) * 111.32 * k, (b[1] - a[1]) * 110.57
        l2 = bx * bx + by * by
        t = 0 if l2 == 0 else max(0, min(1, (px * bx + py * by) / l2))
        d = math.hypot(px - t * bx, py - t * by)
        if d < best[0]:
            best = (d, cum[i] + t * math.sqrt(l2))
    return best

# ------------------------------------------------------ Ember: route data

@app.get("/api/places")
def places(q: str, origin: int | None = None):
    """Search-box typeahead. Pass origin once the start is chosen to get reachable destinations only."""
    return ember_call(planner.typeahead, ember, q, 8, origin)


@app.get("/api/route")
def route(start: str, end: str, date: str | None = None, journey: int = 0):
    """Just the route: two place names in, line + stops out."""
    return route_payload(plan(start, end, date, journey))


@app.get("/api/rejoin")
def rejoin(trip_uid: str, stop_index: int, dwell: int = 60):
    """Hop off at stops[stop_index], spend `dwell` minutes, then the next buses onward."""
    doc = route_cache.get(trip_uid)
    if not doc:
        raise HTTPException(404, "Call /api/route or /api/scenic for this trip first")
    return ember_call(planner.rejoin_options, ember, doc, stop_index, dwell)


# -------------------------------------------------- Ember + LLM: landmarks

@app.get("/api/scenic")
def scenic(start: str, end: str, date: str | None = None, journey: int = 0):
    """Route plus landmarks. The model sees the real stops; its coordinates are checked
    against the real route line and anything more than 5 miles away is dropped."""
    doc = plan(start, end, date, journey)
    payload = route_payload(doc)
    key = ("scenic", payload["trip_uid"])

    if payload["trip_uid"] and key in cache:
        return {"route": payload, "pois": cache[key]}

    stops = payload["stops"]
    names = ", ".join(s["name"] for s in stops if s["name"])

    r = llm.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        temperature=0.3,
        extra_body={"reasoning": {"enabled": False}},
        messages=[
            {"role": "system", "content": (
                "You suggest notable points of interest near a bus route. Each must be within "
                "5 miles of the route. Skip obvious places that are already in the stop list. "
                'Reply with ONLY a JSON array, no other text: [{"name": "...", "lon": 0.0, "lat": 0.0}]. '
                "Max 8 items.")},
            {"role": "user", "content": f"Bus route stops in order: {names}"},
        ],
    )
    raw = parse_json_reply(r.choices[0].message.content)
    if not isinstance(raw, list):
        raise HTTPException(502, "Model returned JSON but not a list")

    line = payload["geometry"]["coordinates"]
    stop_pts = [(s["lon"], s["lat"]) for s in stops]
    valid = [i for i, s in enumerate(stops) if s["lon"] is not None and s["lat"] is not None]
    if not line or not valid:
        return {"route": payload, "pois": []}

    pois = []
    for p in raw:
        try:
            lon, lat = float(p["lon"]), float(p["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        d_route, _ = nearest(lon, lat, line)
        if d_route > 8.05:  # 5 miles
            continue
        d_stop, j = nearest(lon, lat, [stop_pts[i] for i in valid])
        si = valid[j]
        pois.append({
            "name": p.get("name"),
            "lon": lon,
            "lat": lat,
            "km_from_route": round(d_route, 1),
            "nearest_stop_index": si,
            "nearest_stop": stops[si]["name"],
            "km_from_stop": round(d_stop, 1),
        })

    if payload["trip_uid"]:
        cache[key] = pois
    return {"route": payload, "pois": pois}


# --------------------------------------------------------- LLM only

@app.get("/api/pois")
def pois(start: str, end: str):
    key = ("pois", start.lower(), end.lower())
    if key in cache:
        return cache[key]

    a, b = geocode(start), geocode(end)
    pts = thin(route_line(start, end, a, b))
    cum = cumulative(pts)
    total = cum[-1]
    if total == 0:
        return []

    # Skip anything close to the start or end point (about 4 km, less on short routes)
    margin = min(4.0, total * 0.15)
    ends = (pts[0], pts[-1])

    # Search points along the route, plus left/right offsets when the allowed deviation
    # is bigger than Wikipedia's 10 km search radius
    n = min(10, max(1, math.ceil(total / 8)))
    along = [total * i / n for i in range(n + 1)]
    samples = [point_at(pts, cum, s) for s in along]
    if MAX_DEVIATION_KM > 9:
        off = min(MAX_DEVIATION_KM * 0.6, 9.5)
        for s in along:
            samples += side_points(pts, cum, s, off)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(wiki_near, samples))

    # Keep real places within the allowed deviation, not near either end
    found = {}
    for pages in results:
        for p in pages:
            if p.get("coordinates") and p["pageid"] not in found:
                c = p["coordinates"][0]
                if any(km(c["lon"], c["lat"], e[0], e[1]) < margin for e in ends):
                    continue                    # too close to the start or end
                d, along_km = locate((c["lon"], c["lat"]), pts, cum)
                if d <= MAX_DEVIATION_KM:
                    found[p["pageid"]] = {
                        "name": p["title"], "lon": c["lon"], "lat": c["lat"],
                        "description": p.get("description", ""),
                        "image": (p.get("thumbnail") or {}).get("source"),
                        "_d": d, "along_km": round(along_km, 1)}
    if not found:
        return []   # not cached, so a retry can succeed

    # Cap each section of the route so every part is represented
    nb = min(6, max(1, math.ceil(total / 5)))
    bins = [[] for _ in range(nb)]
    for c in found.values():
        bins[min(nb - 1, int(c["along_km"] / total * nb))].append(c)
    cands = []
    for group in bins:
        cands += sorted(group, key=lambda c: c["_d"])[:12]
    cands.sort(key=lambda c: c["along_km"])

    # The model picks by name from real places and is told to spread them out
    listing = "\n".join(
        f"{c['name']} | {c['description']} | {c['along_km']:.0f} km along | {c['_d']:.0f} km off route"
        for c in cands)
    chosen = []
    try:
        r = llm.chat.completions.create(
            model=MODEL, max_tokens=250, temperature=0.2,
            extra_body={"reasoning": {"enabled": False}},
            messages=[
                {"role": "system", "content": (
                    "From the list, pick up to 10 places a traveller would want to visit: castles, ruins, "
                    "beaches, viewpoints, museums, gardens, nature reserves, famous buildings. Skip towns, "
                    "villages, streets, schools, stations, businesses and anything minor. Also skip anything "
                    "inside the start or end town. Prefer notable places over close ones, but do not pick "
                    "places far off the route unless they are clearly worth it. Spread the picks across the "
                    "WHOLE route using the km-along value. Reply with ONLY a JSON array of the exact names "
                    "as written before the first '|'.")},
                {"role": "user", "content": f"Route: {start} to {end} ({total:.0f} km)\n\n{listing}"},
            ],
        )
        picked = set(parse_json_reply(r.choices[0].message.content))
        chosen = [c for c in cands if c["name"] in picked][:10]
    except Exception:
        pass
    if not chosen:                       # model failed or matched nothing: two per section
        chosen = [c for group in bins for c in sorted(group, key=lambda c: c["_d"])[:2]]

    chosen.sort(key=lambda c: c["along_km"])
    data = [{"name": c["name"], "lon": c["lon"], "lat": c["lat"], "image": c["image"],
             "along_km": c["along_km"], "km_off_route": round(c["_d"], 1)} for c in chosen]
    cache[key] = data
    return data

@app.get("/api/poi-info")
def poi_info(poiName: str):
    """~50 word plain-text summary of a place. Call on marker click, not for every marker."""
    key = ("info", poiName.lower())
    if key in cache:
        return cache[key]

    r = llm.chat.completions.create(
        model=MODEL,
        max_tokens=150,
        temperature=0.3,
        extra_body={"reasoning": {"enabled": False}},
        messages=[
            {"role": "system", "content": "Give a summary of about 50 words about a location, plain text only."},
            {"role": "user", "content": f"Location: {poiName}"},
        ],
    )
    data = {"name": poiName, "summary": r.choices[0].message.content.strip()}
    cache[key] = data
    return data