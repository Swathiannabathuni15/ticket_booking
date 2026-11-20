"""Ready-made hall layouts (rows of seat types; None = an aisle gap), used by the city seed."""


def _row(letter: str, kind: str, left: int, mid: int, right: int) -> dict:
    return {"row": letter, "seats": [kind] * left + [None] + [kind] * mid + [None] + [kind] * right}


def large_hall() -> list[dict]:
    """272 seats: 17 rows, 3 price tiers, two aisles (3 | 10 | 3)."""
    rows = [_row(c, "platinum", 3, 10, 3) for c in "ABC"]
    rows += [_row(c, "gold", 3, 10, 3) for c in "DEFGHIJKL"]
    rows += [_row(c, "silver", 3, 10, 3) for c in "MNOPQ"]
    return rows


def medium_hall() -> list[dict]:
    """144 seats: 12 rows, 3 tiers, two aisles (2 | 8 | 2)."""
    rows = [_row(c, "premium", 2, 8, 2) for c in "ABC"]
    rows += [_row(c, "gold", 2, 8, 2) for c in "DEFGH"]
    rows += [_row(c, "standard", 2, 8, 2) for c in "IJKL"]
    return rows


def lounge_hall() -> list[dict]:
    """48 recliner seats: 6 rows (2 | 4 | 2)."""
    return [_row(c, "recliner", 2, 4, 2) for c in "ABCDEF"]
