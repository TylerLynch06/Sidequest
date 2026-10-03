"""
Thin wrapper around the Ember public API.

Every public method maps 1:1 to an endpoint in the docs:
https://wics-hackathon-docs-28b89c.gitlab.io/

Java note: a Python class is declared with `class Name:` and every method
takes `self` explicitly (it is `this`). There are no access modifiers —
a leading underscore (`_get`) is the convention for "private".
"""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.ember.to"


class EmberApiError(RuntimeError):
    """Raised when Ember returns a non-2xx response."""


class EmberClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # One Session reuses the TCP connection across calls (like a pooled HttpClient).
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        self._session.headers["User-Agent"] = "side-quest-hackathon/0.1"

    # ------------------------------------------------------------------ core

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        log.debug("GET %s params=%s", url, params)
        resp = self._session.get(url, params=params, timeout=self.timeout)
        if resp.status_code >= 400:
            raise EmberApiError(f"{resp.status_code} {resp.reason} for {resp.url}: {resp.text[:300]}")
        return resp.json()

    # ------------------------------------------------------------- locations

    def search_locations(
        self,
        query: str = "",
        limit: int = 10,
        type_: str = "STOP_AREA",
        origin: int | None = None,
    ) -> list[dict]:
        """GET /v1/locations/search/ — type-ahead over stop names/regions/codes."""
        params: dict[str, Any] = {"query": query, "limit": limit, "type": type_}
        if origin is not None:
            params["origin"] = origin
        return self._get("/v1/locations/search/", params)

    def resolve_locations(self, ids: list[int]) -> list[dict]:
        """GET /v1/locations/search/?ids=… — look up up to 50 locations by ID (bypasses filters)."""
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            out.extend(self._get("/v1/locations/search/", {"ids": ",".join(map(str, chunk)), "type": "all"}))
        return out

    def list_locations(self, type_: str = "all") -> list[dict]:
        """GET /v1/locations/ — every non-test location. STOP_POINTs carry lat/lon."""
        return self._get("/v1/locations/", {"type": type_})

    # ---------------------------------------------------------------- quotes

    def get_quotes(
        self,
        origin: int,
        destination: int,
        departure_from: str,
        departure_to: str,
        adult: int = 1,
    ) -> dict:
        """GET /v1/quotes/ — journeys between two location IDs in a departure window (ISO-8601 UTC)."""
        return self._get(
            "/v1/quotes/",
            {
                "origin": origin,
                "destination": destination,
                "departure_date_from": departure_from,
                "departure_date_to": departure_to,
                "adult": adult,
            },
        )

    # ----------------------------------------------------------------- trips

    def get_trip(self, trip_id: str, include_cancelled: bool = False) -> dict:
        """GET /v1/trips/{id}/?route=true&description=true — ordered stop list + route number."""
        return self._get(
            f"/v1/trips/{trip_id}/",
            {"route": "true", "description": "true", "include_cancelled_stops": str(include_cancelled).lower()},
        )

    def get_trip_geography(self, trip_uid: str) -> dict | None:
        """GET /v1/trips/{uid}/geography/ — encoded polylines keyed 'origin~destination'. None if 404."""
        try:
            return self._get(f"/v1/trips/{trip_uid}/geography/")
        except EmberApiError as e:
            if "404" in str(e):
                log.warning("No stored geography for trip %s", trip_uid)
                return None
            raise
