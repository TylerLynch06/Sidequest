"""
Command-line entry point.

    python -m routing.cli search dun                       # type-ahead demo
    python -m routing.cli journeys Dundee Edinburgh         # list options for today
    python -m routing.cli plan Dundee Edinburgh -o route.json
    python -m routing.cli -v plan Dundee Edinburgh --date 2026-10-04 --journey 2 --dump-raw
    python -m routing.cli rejoin route.json 8 --dwell 90   # hop off at stop 8, 90 min at the landmark

Java note: `if __name__ == "__main__":` is Python's `public static void main`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import planner
from .ember_client import EmberClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routing", description="Side Quest route planner (Ember API)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging incl. every HTTP call")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="type-ahead over Ember stops")
    p_search.add_argument("text")
    p_search.add_argument("--origin", type=int, help="origin location ID to restrict to reachable stops")

    p_journeys = sub.add_parser("journeys", help="list journeys between two places")
    p_journeys.add_argument("origin")
    p_journeys.add_argument("destination")
    p_journeys.add_argument("--date", help="YYYY-MM-DD (default: next 24h)")

    p_plan = sub.add_parser("plan", help="build the full route JSON")
    p_plan.add_argument("origin")
    p_plan.add_argument("destination")
    p_plan.add_argument("--date", help="YYYY-MM-DD (default: next 24h)")
    p_plan.add_argument("--journey", type=int, default=0, help="index from `journeys` output (default 0)")
    p_plan.add_argument("-o", "--out", default="route.json", help="output path")
    p_plan.add_argument("--dump-raw", action="store_true", help="also save raw API responses to raw/")

    p_rejoin = sub.add_parser("rejoin", help="next buses onward from a hop-off stop in a route.json")
    p_rejoin.add_argument("route_json", help="path to a route.json produced by `plan`")
    p_rejoin.add_argument("stop_index", type=int, help="index into selected_journey.stops")
    p_rejoin.add_argument("--dwell", type=int, default=60, help="minutes at the landmark (default 60)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    client = EmberClient()

    try:
        if args.cmd == "search":
            for hit in planner.typeahead(client, args.text, origin_id=args.origin):
                print(f'{hit["id"]:>5}  {hit["label"]}')
            return 0

        if args.cmd == "journeys":
            origin = planner.search_place(client, args.origin)
            dest = planner.search_place(client, args.destination)
            print(f'{origin["name"]} ({origin["id"]}) -> {dest["name"]} ({dest["id"]})')
            for j in planner.find_journeys(client, origin["id"], dest["id"], args.date):
                price = f'£{j["price_adult_pence"] / 100:.2f}' if j["price_adult_pence"] is not None else "?"
                print(f'[{j["index"]}] {j["departure"]} -> {j["arrival"]}  route {j["route_number"]}  '
                      f'{price}  trip={j["trip_uid"]}')
            return 0

        if args.cmd == "plan":
            if args.dump_raw:
                _enable_raw_dump(client)
            doc = planner.plan_route(client, args.origin, args.destination, args.date, args.journey)
            out = Path(args.out)
            out.write_text(json.dumps(doc, indent=2))
            sel = doc["selected_journey"]
            print(f'Wrote {out}  —  {len(sel["stops"])} stops, '
                  f'{len(sel["geometry"]["coordinates"])} geometry points '
                  f'({sel["geometry_source"]})')
            return 0

        if args.cmd == "rejoin":
            doc = json.loads(Path(args.route_json).read_text())
            stop = doc["selected_journey"]["stops"][args.stop_index]
            print(f'Hop off at {stop["name"]} ({stop["arrival"]}), dwell {args.dwell} min, then:')
            for j in planner.rejoin_options(client, doc, args.stop_index, args.dwell):
                price = f'£{j["price_adult_pence"] / 100:.2f}' if j["price_adult_pence"] is not None else "?"
                print(f'  {j["departure"]} -> {j["arrival"]}  {price}  seats={j["seats_available"]}  '
                      f'board at {j["board_at"]["name"]}')
            return 0

    except Exception as e:  # noqa: BLE001 — hackathon: surface everything plainly
        logging.error("%s", e)
        return 1
    return 0


def _enable_raw_dump(client: EmberClient) -> None:
    """Monkey-patch the client so every response is also written to raw/<n>.json for inspection."""
    raw_dir = Path("raw")
    raw_dir.mkdir(exist_ok=True)
    original = client._get
    counter = {"n": 0}

    def wrapped(path, params=None):
        data = original(path, params)
        counter["n"] += 1
        name = path.strip("/").replace("/", "_") or "root"
        (raw_dir / f'{counter["n"]:02d}_{name}.json').write_text(json.dumps(data, indent=2))
        return data

    client._get = wrapped  # type: ignore[method-assign]


if __name__ == "__main__":
    sys.exit(main())
