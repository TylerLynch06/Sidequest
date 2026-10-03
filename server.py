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

# ------------------------------------------------------------------ setup

load_dotenv(pathlib.Path(__file__).parent / ".env")

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


def nearest(lon, lat, points):
    """(distance_km, index) of the closest [lon, lat] point."""
    return min((km(lon, lat, x, y), i) for i, (x, y) in enumerate(points))


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
    """LLM-only POI list. Coordinates are NOT validated; prefer /api/scenic."""
    key = ("pois", start.lower(), end.lower())
    if key in cache:
        return cache[key]

    r = llm.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        temperature=0.3,
        extra_body={"reasoning": {"enabled": False}},
        messages=[
            {"role": "system", "content": (
                "You suggest points of interest near a route. Each POI is at most 5 miles "
                "from the route; skip obvious places on the route itself. Reply with ONLY a "
                'JSON array, no other text: [{"name": "...", "lon": 0.0, "lat": 0.0}]. Max 8 items.')},
            {"role": "user", "content": f"Route: {start} to {end}"},
        ],
    )
    data = parse_json_reply(r.choices[0].message.content)
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