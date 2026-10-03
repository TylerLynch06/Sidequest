"""End-to-end test against a fake Ember client — no network."""
from routing import planner, polyline


DUNDEE_AREA = {"id": 13, "type": "STOP_AREA", "name": "Dundee (City Centre)", "region_name": "Dundee", "detailed_name": "City Centre"}
DUNDEE_PT = {"id": 1301, "type": "STOP_POINT", "name": "Dundee Slessor Gardens", "region_name": "Dundee", "area_id": 13, "lat": 56.4603, "lon": -2.9676}
PERTH_PT = {"id": 1601, "type": "STOP_POINT", "name": "Perth Broxden", "region_name": "Perth", "area_id": 160, "lat": 56.3832, "lon": -3.4720}
KINROSS_PT = {"id": 1701, "type": "STOP_POINT", "name": "Kinross P&R", "region_name": "Kinross", "area_id": 17, "lat": 56.2069, "lon": -3.4256}
EDIN_AREA = {"id": 42, "type": "STOP_AREA", "name": "Edinburgh (City Centre)", "region_name": "Edinburgh", "detailed_name": "City Centre"}
EDIN_PT = {"id": 4201, "type": "STOP_POINT", "name": "Edinburgh Elder St", "region_name": "Edinburgh", "area_id": 42, "lat": 55.9557, "lon": -3.1903}


class FakeClient:
    def search_locations(self, query="", limit=10, type_="STOP_AREA", origin=None):
        return {"dundee": [DUNDEE_AREA], "edinburgh": [EDIN_AREA]}.get(query.lower(), [])

    def list_locations(self, type_="all"):
        return [DUNDEE_AREA, DUNDEE_PT, PERTH_PT, KINROSS_PT, EDIN_AREA, EDIN_PT]

    def get_quotes(self, origin, destination, departure_from, departure_to, adult=1):
        return {"quotes": [{
            "trip_uid": "TRIP-1",
            "prices": {"adult": 1250},
            "availability": {"seat": 20},
            "legs": [{
                "type": "scheduled_transit",
                "origin": DUNDEE_PT, "destination": EDIN_PT,
                "departure": {"scheduled": "2026-10-04T09:00:00Z"},
                "arrival": {"scheduled": "2026-10-04T10:45:00Z"},
                "description": {"route_number": "E3"},
            }],
        }]}

    def get_trip(self, trip_id, include_cancelled=False):
        def entry(loc, t):
            return {"location": loc, "arrival": {"scheduled": t}, "departure": {"scheduled": t},
                    "allow_boarding": True, "allow_drop_off": True}
        return {"description": {"route_number": "E3"}, "route": [
            entry(DUNDEE_PT, "2026-10-04T09:00:00Z"),
            entry(PERTH_PT, "2026-10-04T09:35:00Z"),
            entry(KINROSS_PT, "2026-10-04T09:55:00Z"),
            entry(EDIN_PT, "2026-10-04T10:45:00Z"),
        ]}

    def get_trip_geography(self, trip_uid):
        def seg(a, b):
            mid = [(a["lon"] + b["lon"]) / 2 + 0.01, (a["lat"] + b["lat"]) / 2]
            return polyline.encode([[a["lon"], a["lat"]], mid, [b["lon"], b["lat"]]])
        return {"id": 1, "stops": [1301, 1601, 1701, 4201], "paths": {
            "1301~1601": seg(DUNDEE_PT, PERTH_PT),
            "1601~1701": seg(PERTH_PT, KINROSS_PT),
            # 1701~4201 deliberately missing -> straight-line fallback for that segment
        }}


def test_plan_route_end_to_end():
    doc = planner.plan_route(FakeClient(), "Dundee", "Edinburgh", "2026-10-04")
    sel = doc["selected_journey"]
    assert doc["origin"]["id"] == 13 and doc["destination"]["id"] == 42
    assert sel["trip_uid"] == "TRIP-1"
    assert [s["name"] for s in sel["stops"]] == ["Dundee Slessor Gardens", "Perth Broxden", "Kinross P&R", "Edinburgh Elder St"]
    assert [s["role"] for s in sel["stops"]] == ["origin", "intermediate", "intermediate", "destination"]
    assert [seg["source"] for seg in sel["segments"]] == ["ember_geography", "ember_geography", "straight_line"]
    coords = sel["geometry"]["coordinates"]
    assert coords[0] == [DUNDEE_PT["lon"], DUNDEE_PT["lat"]]
    assert coords[-1] == [EDIN_PT["lon"], EDIN_PT["lat"]]
    assert len(coords) == 3 + 2 + 1  # 3 pts, +2 (join dropped), +1 (straight line end)
    assert sel["bbox"] is not None
    assert "_raw" not in doc["journeys"][0]


def test_rejoin_options_uses_area_and_dwell():
    client = FakeClient()
    doc = planner.plan_route(client, "Dundee", "Edinburgh", "2026-10-04")
    captured = {}
    def spy(origin, destination, departure_from, departure_to, adult=1):
        captured.update(origin=origin, destination=destination, departure_from=departure_from)
        return FakeClient().get_quotes(origin, destination, departure_from, departure_to)
    client.get_quotes = spy
    opts = planner.rejoin_options(client, doc, stop_index=1, dwell_minutes=90)
    assert captured["origin"] == 160          # Perth's area_id, not the stop point id
    assert captured["destination"] == 42
    assert captured["departure_from"] == "2026-10-04T11:05:00Z"   # 09:35 arrival + 90 min
    assert opts and opts[0]["trip_uid"] == "TRIP-1"
