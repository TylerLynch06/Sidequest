"""
Decode Google-style encoded polylines.

Ember encodes trip geography at precision 6 and in (longitude, latitude)
order — the opposite of Google's default (lat, lon). We decode the raw
pairs and keep them in Ember's order, which is also GeoJSON order.

Reference: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
"""

from __future__ import annotations


def decode(encoded: str, precision: int = 6) -> list[list[float]]:
    """
    Return a list of [first, second] pairs exactly as encoded.
    For Ember strings that is [lon, lat].
    """
    factor = 10 ** precision
    coords: list[list[float]] = []
    index = 0
    first = 0
    second = 0
    length = len(encoded)

    while index < length:
        first_delta, index = _read_varint(encoded, index)
        second_delta, index = _read_varint(encoded, index)
        first += first_delta
        second += second_delta
        coords.append([first / factor, second / factor])

    return coords


def _read_varint(encoded: str, index: int) -> tuple[int, int]:
    """Read one zig-zag, base-64-ish varint starting at `index`. Returns (value, next_index)."""
    result = 0
    shift = 0
    while True:
        b = ord(encoded[index]) - 63
        index += 1
        result |= (b & 0x1F) << shift
        shift += 5
        if b < 0x20:
            break
    # Undo the sign trick: negative numbers were stored as ~(n << 1)
    value = ~(result >> 1) if (result & 1) else (result >> 1)
    return value, index


def encode(coords: list[list[float]], precision: int = 6) -> str:
    """Inverse of decode — handy for tests and for shipping compact geometry to a frontend."""
    factor = 10 ** precision
    out: list[str] = []
    prev_a = prev_b = 0
    for a, b in coords:
        ia, ib = round(a * factor), round(b * factor)
        out.append(_write_varint(ia - prev_a))
        out.append(_write_varint(ib - prev_b))
        prev_a, prev_b = ia, ib
    return "".join(out)


def _write_varint(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chunks: list[str] = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)
