"""
Route planner for Side Quest.

Pipeline:
    text -> Ember location ID      (search_place / typeahead)
    IDs  -> journeys (quotes)      (find_journeys)
    journey -> stops + geometry    (build_route)
    -> one normalised dict         (plan_route) that the rest of the team consumes

Java notes:
  * `dict` is a HashMap, `list` is an ArrayList. `d.get("k")` returns None instead of throwing.
  * `x or y` returns x if it is truthy, else y — used below for "first non-empty".
  * Type hints (`-> dict`) are documentation only; nothing is enforced at runtime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import polyline
from .ember_client import EmberClient

log = logging.getLogger(__name__)

# Keys we will accept as "the trip identifier" on a quote leg. The OpenAPI schema
# doesn't list one, but the docs say a quote yields a trip UID, so we probe.
_TRIP_KEY_CANDIDATES = ("trip_uid", "trip_id", "trip", "uid", "trip_uuid")


# ============================================================ locations


def typeahead(client: EmberClient, text: str, limit: int = 8, origin_id: int | None = None) -> list[dict]:
    """
    Minimal list for a search box: [{id, name, region, label}].
    Pass origin_id when the user has already chosen a start so that only
    reachable destinations are returned.
    """
    text = text.strip()
    results = client.search_locations(text, limit=limit, origin=origin_id)
    return [
        {
            "id": loc["id"],
            "name": loc.get("name", ""),
            "region": loc.get("region_name", ""),
            "label": f'{loc.get("name", "")} — {loc.get("detailed_name", "")}'.rstrip(" —"),
        }
        for loc in results
    ]


def search_place(client: EmberClient, text: str) -> dict:
    """Best-match a free-text place to one Ember STOP_AREA. Raises ValueError if nothing matches."""
    text = text.strip()
    if not text:
        raise ValueError("Empty place name")
    results = client.search_locations(text, limit=5) or client.search_locations(text, limit=5, type_="all")
    if not results:
        raise ValueError(f"No Ember stop matches '{text}'")
    # Prefer an exact (case-insensitive) hit on region or name before falling back to the top result.
    lowered = text.lower()
    for loc in results:
        if lowered in (loc.get("region_name", "").lower(), loc.get("name", "").lower()):
            return loc
    return results[0]


# ============================================================= journeys


def departure_window(travel_date: str | None) -> tuple[str, str]:
    """
    ('YYYY-MM-DD' or None) -> (from_iso, to_iso) in UTC.
    No date = from now until the same time tomorrow.
    """
    if travel_date:
        start = datetime.strptime(travel_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    else:
        start = datetime.now(timezone.utc).replace(microsecond=0)
        end = start + timedelta(days=1)
    return _iso(start), _iso(end)


def find_journeys(client: EmberClient, origin_id: int, destination_id: int, travel_date: str | None = None) -> list[dict]:
    """
    All direct journeys in the window, summarised for a picker UI.
    Each entry keeps `_raw` (the full quote) so build_route() can use it.
    """
    dep_from, dep_to = departure_window(travel_date)
    data = client.get_quotes(origin_id, destination_id, dep_from, dep_to)
    journeys: list[dict] = []
    for i, quote in enumerate(data.get("quotes", [])):
        legs = quote.get("legs", [])
        if not legs:
            continue
        first, last = legs[0], legs[-1]
        journeys.append(
            {
                "index": i,
                "trip_uid": _find_trip_uid(quote, first),
                "route_number": _dig(first, "description", "route_number"),
                "departure": _dig(first, "departure", "scheduled"),
                "arrival": _dig(last, "arrival", "scheduled"),
                "origin_stop": _stop_summary(first.get("origin", {})),
                "destination_stop": _stop_summary(last.get("destination", {})),
                "price_adult_pence": _dig(quote, "prices", "adult"),
                "seats_available": _dig(quote, "availability", "seat"),
                "legs": len(legs),
                "_raw": quote,
            }
        )
    return journeys


# ================================================================ route


def build_route(client: EmberClient, journey: dict, location_index: dict[int, dict] | None = None) -> dict:
    """
    For one chosen journey, return:
        stops    — ordered stops between the user's origin and destination (inclusive)
        geometry — GeoJSON LineString following the road (falls back to straight lines)
        segments — per stop-pair coordinate runs, so the landmark team can say
                   "the POI is between stop N and stop N+1"
    """
    raw = journey["_raw"]
    leg = raw["legs"][0]
    origin_stop = leg["origin"]
    dest_stop = leg["destination"]
    trip_uid = journey.get("trip_uid")

    if location_index is None:
        location_index = build_location_index(client)

    stops, trip_desc = _stops_for_journey(client, trip_uid, origin_stop, dest_stop, location_index)
    geography = client.get_trip_geography(trip_uid) if trip_uid else None
    segments = _segments(stops, geography)

    coords: list[list[float]] = []
    for seg in segments:
        run = seg["coordinates"]
        # Drop the duplicate join point between consecutive segments.
        coords.extend(run[1:] if coords and run and run[0] == coords[-1] else run)
    coords = _dedupe_consecutive(coords)

    return {
        "trip_uid": trip_uid,
        "route_number": journey.get("route_number") or trip_desc.get("route_number"),
        "departure": journey.get("departure"),
        "arrival": journey.get("arrival"),
        "price_adult_pence": journey.get("price_adult_pence"),
        "stops": stops,
        "geometry": {"type": "LineString", "coordinates": coords},
        "geometry_source": "ember_geography" if geography else "straight_line_fallback",
        "segments": segments,
        "bbox": _bbox(coords),
    }


def plan_route(
    client: EmberClient,
    origin_text: str,
    destination_text: str,
    travel_date: str | None = None,
    journey_index: int = 0,
) -> dict:
    """One-shot: two place names in, full route document out."""
    origin = search_place(client, origin_text)
    destination = search_place(client, destination_text)
    journeys = find_journeys(client, origin["id"], destination["id"], travel_date)
    if not journeys:
        raise ValueError(
            f"No Ember journeys from {origin['name']} to {destination['name']} in that window"
        )
    if journey_index >= len(journeys):
        raise ValueError(f"journey_index {journey_index} out of range (found {len(journeys)})")

    chosen = journeys[journey_index]
    route = build_route(client, chosen)

    return {
        "schema_version": 1,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "query": {
            "origin_text": origin_text,
            "destination_text": destination_text,
            "travel_date": travel_date,
            "journey_index": journey_index,
        },
        "origin": _place_summary(origin),
        "destination": _place_summary(destination),
        "journeys": [{k: v for k, v in j.items() if k != "_raw"} for j in journeys],
        "selected_journey": route,
    }


# =============================================================== rejoin


def rejoin_options(
    client: EmberClient,
    route_doc: dict,
    stop_index: int,
    dwell_minutes: int = 60,
    max_results: int = 5,
) -> list[dict]:
    """
    The user hops off at stops[stop_index], spends `dwell_minutes` at a landmark,
    then wants to continue to the original destination. Return the next journeys
    from that stop's area to the destination, earliest first.

    Only stops with allow_boarding=True are valid rejoin points; if the hop-off
    stop can't be boarded, callers should pass the index of the next boardable stop
    (the walking slice decides which one).
    """
    sel = route_doc["selected_journey"]
    stops = sel["stops"]
    if not (0 <= stop_index < len(stops) - 1):
        raise ValueError("stop_index must be an intermediate stop, not the destination")
    stop = stops[stop_index]
    if stop.get("allow_boarding") is False:
        raise ValueError(f"{stop['name']} does not allow boarding; choose a boardable stop")

    origin_id = stop.get("area_id") or stop["location_id"]
    dest_id = route_doc["destination"]["id"]
    arrive = datetime.fromisoformat(stop["arrival"])
    earliest = arrive + timedelta(minutes=dwell_minutes)
    dep_from = _iso(earliest.astimezone(timezone.utc))
    dep_to = _iso((earliest + timedelta(hours=12)).astimezone(timezone.utc))

    data = client.get_quotes(origin_id, dest_id, dep_from, dep_to)
    out: list[dict] = []
    for quote in data.get("quotes", []):
        legs = quote.get("legs", [])
        if not legs:
            continue
        out.append(
            {
                "trip_uid": _find_trip_uid(quote, legs[0]),
                "board_at": _stop_summary(legs[0].get("origin", {})),
                "departure": _dig(legs[0], "departure", "scheduled"),
                "arrival": _dig(legs[-1], "arrival", "scheduled"),
                "price_adult_pence": _dig(quote, "prices", "adult"),
                "seats_available": _dig(quote, "availability", "seat"),
            }
        )
        if len(out) >= max_results:
            break
    return out


# ============================================================= internals


def build_location_index(client: EmberClient) -> dict[int, dict]:
    """id -> location for every Ember location (areas and points). One call, cache it."""
    return {loc["id"]: loc for loc in client.list_locations("all")}


def _stops_for_journey(client, trip_uid, origin_stop, dest_stop, location_index) -> tuple[list[dict], dict]:
    """
    Ordered stops from origin to destination with coordinates and scheduled times,
    plus the trip's description block (route number etc).
    Uses the trip's full stop list when we have a trip UID; otherwise just the two ends.
    """
    if not trip_uid:
        log.warning("No trip UID on quote — route will only contain origin and destination")
        return [_stop_record(0, origin_stop, location_index, role="origin"),
                _stop_record(1, dest_stop, location_index, role="destination")], {}

    trip = client.get_trip(trip_uid)
    trip_desc = trip.get("description") or {}
    route = trip.get("route", [])
    o_idx = _index_of_stop(route, origin_stop)
    d_idx = _index_of_stop(route, dest_stop)
    if o_idx is None or d_idx is None or d_idx <= o_idx:
        log.warning("Could not slice trip %s between %s and %s; using full trip", trip_uid,
                    origin_stop.get("id"), dest_stop.get("id"))
        o_idx, d_idx = 0, len(route) - 1

    stops: list[dict] = []
    for seq, entry in enumerate(route[o_idx : d_idx + 1]):
        loc = entry.get("location", {})
        role = "origin" if seq == 0 else "destination" if entry is route[d_idx] else "intermediate"
        rec = _stop_record(seq, loc, location_index, role=role)
        rec["arrival"] = _dig(entry, "arrival", "scheduled")
        rec["departure"] = _dig(entry, "departure", "scheduled")
        rec["allow_drop_off"] = entry.get("allow_drop_off")
        rec["allow_boarding"] = entry.get("allow_boarding")
        stops.append(rec)
    return stops, trip_desc


def _index_of_stop(route: list[dict], target: dict) -> int | None:
    """Find a leg endpoint in the trip's route. Matches on stop-point id OR its parent area id."""
    wanted = {target.get("id"), target.get("area_id")} - {None}
    for i, entry in enumerate(route):
        loc = entry.get("location", {})
        if loc.get("id") in wanted or loc.get("area_id") in wanted:
            return i
    return None


def _stop_record(seq: int, loc: dict, location_index: dict[int, dict], role: str) -> dict:
    """Normalise a stop, pulling lat/lon from the location index if the trip entry lacks them."""
    full = location_index.get(loc.get("id"), {})
    lat = loc.get("lat") or full.get("lat")
    lon = loc.get("lon") or full.get("lon")
    # STOP_AREAs often have no coordinates; borrow them from the first child STOP_POINT.
    if lat is None or lon is None:
        for cand in location_index.values():
            if cand.get("area_id") == loc.get("id") and cand.get("lat") is not None:
                lat, lon = cand["lat"], cand["lon"]
                break
    return {
        "sequence": seq,
        "role": role,
        "location_id": loc.get("id"),
        "area_id": loc.get("area_id") or full.get("area_id"),
        "name": loc.get("name") or full.get("name"),
        "region": loc.get("region_name") or full.get("region_name"),
        "detailed_name": loc.get("detailed_name") or full.get("detailed_name"),
        "lat": lat,
        "lon": lon,
    }


def _segments(stops: list[dict], geography: dict | None) -> list[dict]:
    """
    One entry per consecutive stop pair. Coordinates come from Ember's encoded
    polyline for that pair when available, else a straight line.
    """
    paths = (geography or {}).get("paths", {}) or {}
    geo_stops: list[int] = (geography or {}).get("stops") or []
    segments: list[dict] = []

    for a, b in zip(stops, stops[1:]):
        coords = _path_between(a, b, paths, geo_stops)
        if coords is None:
            coords = [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]
            source = "straight_line"
        else:
            source = "ember_geography"
        segments.append(
            {
                "from_stop_id": a["location_id"],
                "to_stop_id": b["location_id"],
                "from_name": a["name"],
                "to_name": b["name"],
                "source": source,
                "coordinates": coords,
            }
        )
    return segments


def _path_between(a: dict, b: dict, paths: dict[str, str], geo_stops: list[int]) -> list[list[float]] | None:
    """
    Geography keys are 'origin_id~destination_id' between ADJACENT geography stops.
    Our stop list may use area IDs or skip stops, so walk the geography stop list
    from a to b and concatenate every adjacent path on the way.
    """
    ids_a = {a["location_id"], a.get("area_id")} - {None}
    ids_b = {b["location_id"], b.get("area_id")} - {None}

    # Direct hit first.
    for x in ids_a:
        for y in ids_b:
            key = f"{x}~{y}"
            if key in paths:
                return polyline.decode(paths[key])

    # Otherwise walk the geography stop sequence.
    if not geo_stops:
        return None
    start = next((i for i, s in enumerate(geo_stops) if s in ids_a), None)
    end = next((i for i, s in enumerate(geo_stops) if s in ids_b and (start is None or i > start)), None)
    if start is None or end is None:
        return None

    coords: list[list[float]] = []
    for s, t in zip(geo_stops[start:end], geo_stops[start + 1 : end + 1]):
        run = paths.get(f"{s}~{t}")
        if run is None:
            return None
        decoded = polyline.decode(run)
        coords.extend(decoded[1:] if coords and decoded and decoded[0] == coords[-1] else decoded)
    return coords or None


def _find_trip_uid(quote: dict, leg: dict) -> str | None:
    for container in (leg, quote):
        for key in _TRIP_KEY_CANDIDATES:
            val = container.get(key)
            if isinstance(val, (str, int)):
                return str(val)
            if isinstance(val, dict) and val.get("uid"):
                return str(val["uid"])
    # Last resort: any key that mentions 'trip' with a scalar value.
    for key, val in leg.items():
        if "trip" in key.lower() and isinstance(val, (str, int)):
            return str(val)
    log.warning("No trip identifier found on quote leg. Leg keys: %s", sorted(leg.keys()))
    return None


def _stop_summary(loc: dict) -> dict:
    return {
        "id": loc.get("id"),
        "area_id": loc.get("area_id"),
        "name": loc.get("name"),
        "region": loc.get("region_name"),
        "lat": loc.get("lat"),
        "lon": loc.get("lon"),
    }


def _place_summary(loc: dict) -> dict:
    return {
        "id": loc.get("id"),
        "type": loc.get("type"),
        "name": loc.get("name"),
        "region": loc.get("region_name"),
        "detailed_name": loc.get("detailed_name"),
    }


def _dedupe_consecutive(coords: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for c in coords:
        if not out or c != out[-1]:
            out.append(c)
    return out


def _bbox(coords: list[list[float]]) -> list[float] | None:
    pts = [c for c in coords if c[0] is not None and c[1] is not None]
    if not pts:
        return None
    lons = [c[0] for c in pts]
    lats = [c[1] for c in pts]
    return [min(lons), min(lats), max(lons), max(lats)]


def _dig(d: Any, *keys: str) -> Any:
    """Safe nested lookup: _dig(x, 'a', 'b') == x['a']['b'] or None."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
