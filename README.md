# SIDE QUEST! — Ember Hackathon

Plan an Ember bus journey, then discover landmarks along the route: hop off at
one stop, walk to the landmark, rejoin at the next stop.

## Team slices

| Slice | Owner | Status |
| --- | --- | --- |
| Start/end selection + Ember routing → `route.json` | Jason | ✅ in `routing/` |
| Landmarks / POIs along the route | | |
| Walking directions stop → landmark → next stop | | |
| UI | | |

---

## `routing/` — journey planner (Python)

Turns two place names into one normalised JSON document the other slices consume.

### Setup

```sh
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest                                      # offline tests, no network needed
```

### Use it

```sh
python -m routing.cli search dun                      # type-ahead over Ember stops
python -m routing.cli journeys Dundee Edinburgh       # list today's journeys
python -m routing.cli plan Dundee Edinburgh -o route.json
python -m routing.cli -v plan Dundee Edinburgh --date 2026-10-04 --journey 2 --dump-raw
python -m routing.cli rejoin route.json 8 --dwell 90   # next buses onward after a 90-min stop at stop 8
```

`--dump-raw` saves every API response under `raw/` — use it the first time to
confirm what the live quote actually contains (see *Known risk* below).

From Python:

```python
from routing import EmberClient, plan_route, typeahead, find_journeys

client = EmberClient()
typeahead(client, "edin")                              # -> [{id, name, region, label}, ...]
doc = plan_route(client, "Dundee", "Edinburgh", "2026-10-04", journey_index=0)
```

### Output: `route.json`

See [`examples/route.example.json`](examples/route.example.json). Shape:

```
schema_version, generated_at
query            { origin_text, destination_text, travel_date, journey_index }
origin / destination   { id, type, name, region, detailed_name }   # Ember STOP_AREA
journeys[]       every option in the window: trip_uid, route_number, departure,
                 arrival, price_adult_pence, seats_available, origin_stop, destination_stop
selected_journey
  trip_uid, route_number, departure, arrival, price_adult_pence
  stops[]        ORDERED, origin→destination inclusive
                   sequence, role (origin|intermediate|destination), location_id, area_id,
                   name, region, lat, lon, arrival, departure, allow_drop_off, allow_boarding
  geometry       GeoJSON LineString, coordinates are [lon, lat]
  geometry_source  "ember_geography" | "straight_line_fallback"
  segments[]     one per consecutive stop pair: from_stop_id, to_stop_id, from_name,
                 to_name, source, coordinates[[lon,lat],...]
  bbox           [min_lon, min_lat, max_lon, max_lat]
```

**For the landmark slice:** search for POIs near `selected_journey.geometry` (or
per `segments[]` so you know which two stops bracket the landmark). Only stops
with `allow_drop_off: true` are valid hop-off points; `allow_boarding: true` for rejoining.

**For the walking slice:** stop coordinates are in `stops[].lat/lon`.

**Rejoining after a side quest:** `rejoin_options(client, route_doc, stop_index, dwell_minutes)`
quotes the next buses from that stop's area to the original destination, departing after
`arrival + dwell`. Only stops with `allow_boarding: true` can be rejoin points.

**Times are UTC** (`+00:00`). Convert to `Europe/London` before showing to a user.

### Ember endpoints used

| Step | Endpoint |
| --- | --- |
| Type-ahead / resolve place | `GET /v1/locations/search/?query=…` |
| All stops with coordinates | `GET /v1/locations/?type=all` |
| Journeys between two IDs | `GET /v1/quotes/?origin=&destination=&departure_date_from=&departure_date_to=` |
| Ordered stop list for a trip | `GET /v1/trips/{uid}/?route=true` |
| Road geometry | `GET /v1/trips/{uid}/geography/` (polylines, precision 6, **lon/lat order**) |

No auth needed for any of these. Docs: https://wics-hackathon-docs-28b89c.gitlab.io/

### Verified live (2026-10-03)

Quote legs carry `trip_uid`; trip info and geography both resolve. Dundee → Edinburgh
returns 12 stops with road geometry. `seats_available` can be 0 — grey those out in the UI.

---

## Python for a Java dev — the 60-second version

| Java | Python |
| --- | --- |
| `public class Foo { ... }` | `class Foo:` — no braces, indentation is the block |
| `this.x` | `self.x` — `self` is an explicit first parameter on every method |
| `Map<String,Object>` | `dict` — `d["k"]` throws, `d.get("k")` returns `None` |
| `List<T>` | `list` — `lst[1:]` is "everything from index 1" (slicing) |
| `null` | `None` |
| `String.format("%s", x)` | `f"{x}"` (f-strings) |
| `for (T t : items)` | `for t in items:` |
| `try { } catch (E e) { }` | `try: ... except E as e:` |
| `import a.b.C;` | `from a.b import C` |
| `public static void main` | `if __name__ == "__main__":` |
| checked exceptions | none — everything is unchecked |
| private/protected | convention only: `_name` means "don't touch" |
| `x != null ? x : y` | `x or y` |

Run any file with `python file.py`; run a package module with `python -m package.module`.
