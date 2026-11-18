"""Choosing good seats: how well will you SEE from here, and what does it cost?

Everything works from a hall's seat list (labels like "H7", where row A is the BACK and the last
letter is nearest the screen, and a missing number in a row is an aisle). The same simple model of
the hall is used by the 3D view in the website (frontend/src/components/hallLayout.ts), so the
numbers here match what you see there. It is a model, not a measurement of a real cinema.

HOW A SEAT IS SCORED (0 to 100, higher is better), from three things:
  screen size   how much of your view the screen fills. Too close = overwhelming, too far = small.
                Comfortable is roughly 38 to 58 degrees wide.
  side angle    how far off-centre you sit; a seat well to the side sees a stretched picture.
  neck angle    how far you must tilt your head up to see the top of the screen (front rows).
A group of seats is scored by the average of its seats, then adjusted for the "real life" cases:
  - a lone empty seat left beside your group (nobody can book it: a small penalty)
  - an aisle seat, when you asked for one (a small bonus)
  - price: used to pick the best-VALUE and cheapest options, never to hide the best-VIEW one.
"""

import math
from dataclasses import dataclass

from app.schemas import SeatOut

# metres: keep in step with frontend/src/components/hallLayout.ts
ROW_DEPTH, PITCH, SCREEN_GAP, RISE, EYE, SCREEN_BOTTOM = 1.1, 0.55, 3.0, 0.22, 1.1, 1.4
COMFORT_FOV = (38.0, 58.0)
ORPHAN_PENALTY, AISLE_BONUS = 10.0, 6.0
SWEET_SPOT_FOV = 48.0  # many rows can all be "perfect"; the best view is the one nearest this
GOOD_ENOUGH = 55.0  # the "cheapest" option must be at least this good to see from


def split_label(label: str) -> tuple[str, int]:
    i = next(k for k, ch in enumerate(label) if ch.isdigit())
    return label[:i], int(label[i:])


@dataclass(frozen=True)
class Hall:
    rows: list[str]  # sorted; rows[0] is the back
    n_cols: int
    screen_w: float
    screen_h: float
    present: dict[str, set[int]]  # seat numbers that exist in each row (a missing number = aisle)


def build_hall(seats: list[SeatOut]) -> Hall:
    present: dict[str, set[int]] = {}
    for s in seats:
        row, num = split_label(s.label)
        present.setdefault(row, set()).add(num)
    n_cols = max((max(v) for v in present.values()), default=0)
    w = max(9.0, n_cols * PITCH * 1.35)
    return Hall(sorted(present), n_cols, w, w * 0.44, present)


@dataclass(frozen=True)
class Metrics:
    rows_from_screen: int
    distance: float
    fov: float  # degrees of your view the screen fills
    off_axis: float  # degrees to the side
    neck: float  # degrees you look up to the top of the screen
    visibility: float  # 0..100


def seat_metrics(hall: Hall, row: str, num: int) -> Metrics:
    rows_from_screen = len(hall.rows) - 1 - hall.rows.index(row)
    d = SCREEN_GAP + rows_from_screen * ROW_DEPTH
    x = (num - 1 - (hall.n_cols - 1) / 2) * PITCH
    eye = rows_from_screen * RISE + EYE
    fov = math.degrees(2 * math.atan((hall.screen_w / 2) / d))
    off = math.degrees(math.atan(abs(x) / d))
    neck = math.degrees(math.atan(max(0.0, SCREEN_BOTTOM + hall.screen_h - eye) / d))
    lo, hi = COMFORT_FOV
    fov_pen = 0.0 if lo <= fov <= hi else min(1.0, (lo - fov) / 20 if fov < lo else (fov - hi) / 30)
    off_pen = min(1.0, max(0.0, off - 3) / 22)
    neck_pen = min(1.0, max(0.0, neck - 25) / 30)
    visibility = round(100 * (1 - (0.4 * fov_pen + 0.3 * off_pen + 0.3 * neck_pen)), 1)
    return Metrics(rows_from_screen, d, fov, off, neck, visibility)


@dataclass
class Candidate:
    """A group of seats in one row that would be booked together."""

    seats: list[SeatOut]
    row: str
    nums: list[int]
    across_aisle: bool
    visibility: float
    fov: float
    off_axis: float
    neck: float
    rows_from_screen: int
    off_centre: float  # seats left (-) / right (+) of the middle of the hall
    total: float
    each: float
    orphans: int  # lone empty seats this group would leave beside it
    aisle: bool

    @property
    def seat_type(self) -> str:
        return self.seats[0].seat_type

    def utility(self, prefer_aisle: bool = False) -> float:
        """The 'best view' ranking: visibility, then the sweet spot as a tie-breaker, nudged by the real-life cases."""
        sweet = 0.15 * abs(self.fov - SWEET_SPOT_FOV) + 0.2 * self.off_axis
        return (
            self.visibility
            - sweet
            - ORPHAN_PENALTY * self.orphans
            + (AISLE_BONUS if prefer_aisle and self.aisle else 0.0)
        )


def candidates(
    seats: list[SeatOut],
    count: int,
    *,
    allow_aisle_cross: bool = False,
    seat_type: str | None = None,
    hall: Hall | None = None,
) -> list[Candidate]:
    """Every group of `count` seats that are all free, in one row, side by side (and the same price
    tier). With allow_aisle_cross, two blocks separated by exactly one aisle also count."""
    hall = hall or build_hall(seats)
    by_row: dict[str, dict[int, SeatOut]] = {}
    for s in seats:
        row, num = split_label(s.label)
        by_row.setdefault(row, {})[num] = s
    out: list[Candidate] = []
    for row, line in by_row.items():
        free = sorted(
            n for n, s in line.items() if s.status == "available" and (seat_type is None or s.seat_type == seat_type)
        )
        free_set = set(free)
        runs: list[list[int]] = []
        for n in free:
            if runs and n == runs[-1][-1] + 1:
                runs[-1].append(n)
            elif runs and allow_aisle_cross and n == runs[-1][-1] + 2 and (n - 1) not in hall.present[row]:
                runs[-1].append(n)  # one aisle between: still "together"
            else:
                runs.append([n])
        for run in runs:
            for k in range(len(run) - count + 1):
                nums = run[k : k + count]
                group = [line[n] for n in nums]
                if len({g.seat_type for g in group}) > 1:  # never mix price tiers in one booking
                    continue
                crossed = any(b - a == 2 for a, b in zip(nums, nums[1:], strict=False))
                ms = [seat_metrics(hall, row, n) for n in nums]
                lo, hi = nums[0], nums[-1]
                left, right = lo - 1, hi + 1
                orphans = sum(
                    1
                    for edge, beyond in ((left, left - 1), (right, right + 1))
                    if edge in free_set and beyond not in free_set
                )
                aisle = any(n >= 1 and n <= hall.n_cols and n not in hall.present[row] for n in (left, right))
                total = round(sum(g.price for g in group), 2)
                out.append(
                    Candidate(
                        seats=group,
                        row=row,
                        nums=nums,
                        across_aisle=crossed,
                        visibility=round(sum(m.visibility for m in ms) / count, 1),
                        fov=sum(m.fov for m in ms) / count,
                        off_axis=sum(m.off_axis for m in ms) / count,
                        neck=sum(m.neck for m in ms) / count,
                        rows_from_screen=ms[0].rows_from_screen,
                        off_centre=round(sum(nums) / count - (hall.n_cols + 1) / 2, 1),
                        total=total,
                        each=round(total / count, 2),
                        orphans=orphans,
                        aisle=aisle,
                    )
                )
    return out


def best_candidate(
    seats: list[SeatOut],
    count: int,
    seat_type: str | None = None,
    *,
    centre_only: bool = False,
    min_rows_from_screen: int = 0,
    max_price: float | None = None,
) -> Candidate | None:
    """The best-VIEW group, with the filters the movie-page seat finder offers."""
    hall = build_hall(seats)
    band = 0.2 * hall.n_cols  # "centre" = the middle 40% of the row
    best: Candidate | None = None
    for c in candidates(seats, count, seat_type=seat_type, hall=hall):
        if c.rows_from_screen < min_rows_from_screen or (
            max_price is not None and any(s.price > max_price for s in c.seats)
        ):
            continue
        if centre_only and abs(c.off_centre) > band:
            continue
        if best is None or (c.utility(), -c.total) > (best.utility(), -best.total):
            best = c
    return best


# ---- the options a person is shown ---------------------------------------------------------


@dataclass
class Option:
    labels: list[str]  # why it is being offered: "Best view", "Best value", "Cheapest"
    cand: Candidate
    reasons: list[str]
    warnings: list[str]
    over_budget_by: float | None = None


def _reasons(c: Candidate) -> list[str]:
    out = []
    out.append(
        "Right in the centre"
        if abs(c.off_axis) <= 4
        else f"{round(c.off_axis)}° to the side"
        if c.off_axis > 12
        else "Close to the centre"
    )
    if COMFORT_FOV[0] <= c.fov <= COMFORT_FOV[1]:
        out.append(f"Screen fills a comfortable {round(c.fov)}° of your view")
    elif c.fov > COMFORT_FOV[1]:
        out.append(f"Close to the screen ({round(c.fov)}°): it will feel big")
    else:
        out.append(f"Far from the screen ({round(c.fov)}°): it will look smaller")
    if c.neck > 35:
        out.append(f"You'll tilt your head up about {round(c.neck)}°")
    out.append(f"{c.seat_type.title()} seats, ₹{c.each:g} each")
    if c.aisle:
        out.append("Aisle seat: easy to get in and out")
    return out


def _warnings(c: Candidate, over: float | None) -> list[str]:
    w = []
    if c.across_aisle:
        w.append("Split by an aisle: you'll sit together, with a walkway between")
    if c.orphans:
        w.append(
            "Leaves a single empty seat beside your group"
            if c.orphans == 1
            else "Leaves single empty seats on both sides of your group"
        )
    if c.visibility < GOOD_ENOUGH:
        w.append("The view from here is only fair")
    if over:
        w.append(f"₹{over:g} over your budget")
    return w


def rank_options(
    seats: list[SeatOut],
    count: int,
    *,
    max_total: float | None = None,
    max_price: float | None = None,
    prefer_aisle: bool = False,
    seat_type: str | None = None,
) -> tuple[list[Option], str | None]:
    """Up to three different good choices (best view / best value / cheapest) and an optional message
    that explains an empty or compromised answer."""
    if not any(s.status == "available" for s in seats):
        return [], "This show is sold out."
    hall = build_hall(seats)
    cands = candidates(seats, count, seat_type=seat_type, hall=hall)
    note = None
    if not cands:
        cands = candidates(seats, count, allow_aisle_cross=True, seat_type=seat_type, hall=hall)
        if cands:
            note = f"No {count} seats are free side by side, so these are split by an aisle."
    if not cands:
        return [], f"No {count} seats together are free in this show. Try fewer seats, or another show."

    def fits(c: Candidate) -> bool:
        return (max_total is None or c.total <= max_total) and (
            max_price is None or all(s.price <= max_price for s in c.seats)
        )

    feasible = [c for c in cands if fits(c)]
    if not feasible:  # nothing within the budget: show the closest, honestly

        def over(c: Candidate) -> float:
            return max(
                c.total - max_total if max_total is not None else 0.0,
                (max(s.price for s in c.seats) - max_price) * count if max_price is not None else 0.0,
            )

        closest = min(cands, key=lambda c: (over(c), -c.utility(prefer_aisle)))
        gap = round(over(closest), 2)
        budget = f"₹{max_total:g}" if max_total is not None else f"₹{max_price:g} a seat"
        return (
            [Option(["Closest to your budget"], closest, _reasons(closest), _warnings(closest, gap), gap)],
            f"Nothing fits your budget of {budget} for {count} seats. The closest is ₹{closest.total:g}.",
        )

    best_view = max(feasible, key=lambda c: (c.utility(prefer_aisle), -c.total))
    totals = [c.total for c in feasible]
    lo, span = min(totals), (max(totals) - min(totals)) or 1.0
    best_value = max(feasible, key=lambda c: (c.utility(prefer_aisle) - 40 * (c.total - lo) / span, -c.total))
    decent = [c for c in feasible if c.visibility >= GOOD_ENOUGH] or feasible
    cheapest = min(decent, key=lambda c: (c.total, -c.utility(prefer_aisle)))

    picked: list[tuple[list[str], Candidate]] = []
    for label, c in (("Best view", best_view), ("Best value", best_value), ("Cheapest", cheapest)):
        for entry in picked:
            if [s.id for s in entry[1].seats] == [s.id for s in c.seats]:
                entry[0].append(label)  # the same seats win more than one way
                break
        else:
            picked.append(([label], c))
    # When the same seats win every way, offer a couple of genuinely different alternatives (other rows),
    # so there is something to compare. They must still be good to see from.
    used_rows = {c.row for _, c in picked}
    floor = max(GOOD_ENOUGH, best_view.visibility - 15)
    for c in sorted(feasible, key=lambda c: (-c.utility(prefer_aisle), c.total)):
        if len(picked) >= 3:
            break
        if c.row not in used_rows and c.visibility >= floor:
            picked.append((["Also good"], c))
            used_rows.add(c.row)
    return [Option(labels, c, _reasons(c), _warnings(c, None)) for labels, c in picked], note
