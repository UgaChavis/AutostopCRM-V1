"""Deterministic orthogonal routing for the manager structure diagram.

Routes are calculated from semantic endpoints. A failed layout is never saved.
The legacy SVG path remains a portable rendering field, not an input to this router.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from heapq import heappop, heappush
from itertools import count

CLEARANCE = 2
LANE = 4
PARALLEL_GAP = 10
CROSSING_COST = 24
_PATH_TOKEN = re.compile(r"[MLHVQC]|[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_PATH_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "C": 6}


class RouteUnavailable(ValueError):
    def __init__(self, relation_id: str) -> None:
        self.relation_id = relation_id
        super().__init__(f"Нет свободного маршрута для связи {relation_id}.")


def _center(node: dict) -> tuple[float, float]:
    return node["x"] + node["width"] / 2, node["y"] + node["height"] / 2


def _side(node: dict, other: dict) -> str:
    x, y = _center(node)
    ox, oy = _center(other)
    dx, dy = ox - x, oy - y
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "bottom" if dy >= 0 else "top"


def _port(node: dict, side: str, index: int, total: int) -> tuple[float, float]:
    horizontal = side in {"top", "bottom"}
    length = node["width"] if horizontal else node["height"]
    inset = min(14, length / 4)
    position = inset + (length - 2 * inset) * (index + 1) / (total + 1)
    if side == "top":
        return node["x"] + position, node["y"]
    if side == "bottom":
        return node["x"] + position, node["y"] + node["height"]
    if side == "left":
        return node["x"], node["y"] + position
    return node["x"] + node["width"], node["y"] + position


def _outside(port: tuple[float, float], side: str) -> tuple[float, float]:
    if side == "left":
        return port[0] - LANE, port[1]
    if side == "right":
        return port[0] + LANE, port[1]
    if side == "top":
        return port[0], port[1] - LANE
    return port[0], port[1] + LANE


def _segments(points: list[tuple[float, float]]):
    return list(zip(points, points[1:]))


def _parse_svg_path(path: str):
    """Parse the bounded absolute SVG subset used by custom structure routes."""
    if not isinstance(path, str) or not path or len(path) > 2000:
        return None
    tokens = []
    position = 0
    for match in _PATH_TOKEN.finditer(path):
        if not re.fullmatch(r"[\s,]*", path[position : match.start()]):
            return None
        tokens.append(match.group())
        position = match.end()
    if not tokens or not re.fullmatch(r"[\s,]*", path[position:]) or tokens[0] != "M":
        return None
    try:
        start = (float(tokens[1]), float(tokens[2]))
    except (IndexError, ValueError):
        return None
    if not all(math.isfinite(value) for value in start):
        return None
    segments = []
    x, y = start
    index = 3
    while index < len(tokens):
        command = tokens[index]
        if command not in _PATH_ARGS or command == "M":
            return None
        count = _PATH_ARGS[command]
        if index + count >= len(tokens):
            return None
        try:
            values = [float(value) for value in tokens[index + 1 : index + count + 1]]
        except ValueError:
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        if command == "H":
            x = values[0]
            segment = {"command": command, "values": values, "end": (x, y)}
        elif command == "V":
            y = values[0]
            segment = {"command": command, "values": values, "end": (x, y)}
        elif command == "L":
            x, y = values
            segment = {"command": command, "values": values, "end": (x, y)}
        elif command == "Q":
            x, y = values[2:]
            segment = {"command": command, "values": values, "end": (x, y)}
        else:
            x, y = values[4:]
            segment = {"command": command, "values": values, "end": (x, y)}
        segments.append(segment)
        index += count + 1
        if len(segments) > 64:
            return None
    return (start, segments) if segments else None


def _route_endpoints(path: str):
    parsed = _parse_svg_path(path)
    if parsed is None:
        return None
    return parsed[0], parsed[1][-1]["end"]


def _anchor_point(node: dict, anchor: dict) -> tuple[float, float]:
    side, offset = anchor["side"], anchor["offset"]
    if side == "top":
        return node["x"] + node["width"] * offset, node["y"]
    if side == "bottom":
        return node["x"] + node["width"] * offset, node["y"] + node["height"]
    if side == "left":
        return node["x"], node["y"] + node["height"] * offset
    return node["x"] + node["width"], node["y"] + node["height"] * offset


def _anchor_from_point(node: dict, point: tuple[float, float]) -> dict:
    x, y = point
    candidates = [
        (abs(x - node["x"]), "left", (y - node["y"]) / node["height"]),
        (abs(x - node["x"] - node["width"]), "right", (y - node["y"]) / node["height"]),
        (abs(y - node["y"]), "top", (x - node["x"]) / node["width"]),
        (
            abs(y - node["y"] - node["height"]),
            "bottom",
            (x - node["x"]) / node["width"],
        ),
    ]
    _, side, offset = min(candidates, key=lambda candidate: candidate[0])
    return {"side": side, "offset": max(0.0, min(1.0, offset))}


def _reanchor_path(path: str, start: tuple[float, float], end: tuple[float, float]) -> str | None:
    parsed = _parse_svg_path(path)
    if parsed is None:
        return None
    old_start, source_segments = parsed
    if old_start == start and source_segments[-1]["end"] == end:
        return path
    segments = [dict(item, values=list(item["values"])) for item in source_segments]
    first, last = segments[0], segments[-1]

    def preserve_axis_aligned_line(segment, before, after):
        if segment["command"] != "L":
            return
        if abs(before[1] - after[1]) <= 1e-7:
            segment["command"] = "H"
            segment["values"] = [after[0]]
        elif abs(before[0] - after[0]) <= 1e-7:
            segment["command"] = "V"
            segment["values"] = [after[1]]

    preserve_axis_aligned_line(first, old_start, first["end"])
    last_start = old_start if len(segments) == 1 else segments[-2]["end"]
    preserve_axis_aligned_line(last, last_start, last["end"])

    start_delta = (start[0] - old_start[0], start[1] - old_start[1])
    end_delta = (end[0] - segments[-1]["end"][0], end[1] - segments[-1]["end"][1])
    first, last = segments[0], segments[-1]
    if first["command"] == "Q":
        if first is last:
            first["values"][0] += (start_delta[0] + end_delta[0]) / 2
            first["values"][1] += (start_delta[1] + end_delta[1]) / 2
        else:
            first["values"][0] += start_delta[0]
            first["values"][1] += start_delta[1]
    elif first["command"] == "C":
        first["values"][0] += start_delta[0]
        first["values"][1] += start_delta[1]
    if last["command"] == "Q":
        if first is not last:
            last["values"][0] += end_delta[0]
            last["values"][1] += end_delta[1]
        last["values"][2:] = list(end)
    elif last["command"] == "C":
        last["values"][2] += end_delta[0]
        last["values"][3] += end_delta[1]
        last["values"][4:] = list(end)
    elif last["command"] in {"H", "V"}:
        x, y = start
        for segment in segments[:-1]:
            if segment["command"] == "H":
                x = segment["values"][0]
            elif segment["command"] == "V":
                y = segment["values"][0]
            else:
                x, y = segment["end"]
        previous = (x, y)
        if last["command"] == "H":
            if abs(previous[1] - end[1]) > 1e-7:
                segments.pop()
                segments.extend(
                    (
                        {"command": "V", "values": [end[1]], "end": (previous[0], end[1])},
                        {"command": "H", "values": [end[0]], "end": end},
                    )
                )
            else:
                last["values"] = [end[0]]
                last["end"] = end
        elif abs(previous[0] - end[0]) > 1e-7:
            segments.pop()
            segments.extend(
                (
                    {"command": "H", "values": [end[0]], "end": (end[0], previous[1])},
                    {"command": "V", "values": [end[1]], "end": end},
                )
            )
        else:
            last["values"] = [end[1]]
            last["end"] = end
    else:
        last["values"] = list(end)
        last["end"] = end
    chunks = [f"M{_format(start[0])} {_format(start[1])}"]
    for segment in segments:
        chunks.append(segment["command"] + " ".join(_format(value) for value in segment["values"]))
    return " ".join(chunks)


def _intersects(first, second) -> bool:
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second

    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(value) < 1e-7 else 1 if value > 0 else -1

    def on_segment(a, b, point):
        return (
            min(a[0], b[0]) - 1e-7 <= point[0] <= max(a[0], b[0]) + 1e-7
            and min(a[1], b[1]) - 1e-7 <= point[1] <= max(a[1], b[1]) + 1e-7
        )

    o1, o2 = orientation((ax, ay), (bx, by), (cx, cy)), orientation((ax, ay), (bx, by), (dx, dy))
    o3, o4 = orientation((cx, cy), (dx, dy), (ax, ay)), orientation((cx, cy), (dx, dy), (bx, by))
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and on_segment((ax, ay), (bx, by), (cx, cy)))
        or (o2 == 0 and on_segment((ax, ay), (bx, by), (dx, dy)))
        or (o3 == 0 and on_segment((cx, cy), (dx, dy), (ax, ay)))
        or (o4 == 0 and on_segment((cx, cy), (dx, dy), (bx, by)))
    )


def _intersection_point(first, second):
    (px, py), (px2, py2) = first
    (qx, qy), (qx2, qy2) = second
    rx, ry, sx, sy = px2 - px, py2 - py, qx2 - qx, qy2 - qy
    cross = rx * sy - ry * sx
    qpx, qpy = qx - px, qy - py
    if abs(cross) < 1e-7:
        if abs(qpx * ry - qpy * rx) >= 1e-7:
            return None
        shared = [
            point
            for point in (first[0], first[1], second[0], second[1])
            if _intersects((point, point), first) and _intersects((point, point), second)
        ]
        return shared[0] if shared else None
    t = (qpx * sy - qpy * sx) / cross
    u = (qpx * ry - qpy * rx) / cross
    if -1e-7 <= t <= 1 + 1e-7 and -1e-7 <= u <= 1 + 1e-7:
        return px + t * rx, py + t * ry
    return None


def _collinear_overlap(first, second) -> bool:
    a, b = first
    c, d = second
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    offset = (c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])
    if abs(cross) > 1e-7 or abs(offset) > 1e-7:
        return False
    axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
    return (
        max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        < min(max(a[axis], b[axis]), max(c[axis], d[axis])) - 1e-7
    )


def _route_vertices(path: str) -> set[tuple[float, float]]:
    parsed = _parse_svg_path(path)
    if parsed is None:
        return set()
    return {parsed[0], *(segment["end"] for segment in parsed[1])}


def _parallel_gap(first, second) -> float | None:
    """Separation of projected parallel segments, or None when disjoint."""
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second
    if ay == by == cy == dy:
        return 0 if max(min(ax, bx), min(cx, dx)) <= min(max(ax, bx), max(cx, dx)) else None
    if ax == bx == cx == dx:
        return 0 if max(min(ay, by), min(cy, dy)) <= min(max(ay, by), max(cy, dy)) else None
    if ay == by and cy == dy:
        return (
            abs(ay - cy) if max(min(ax, bx), min(cx, dx)) < min(max(ax, bx), max(cx, dx)) else None
        )
    if ax == bx and cx == dx:
        return (
            abs(ax - cx) if max(min(ay, by), min(cy, dy)) < min(max(ay, by), max(cy, dy)) else None
        )
    return None


def _blocked_by_node(a, b, node) -> bool:
    left = node["x"] - CLEARANCE
    top = node["y"] - CLEARANCE
    right = node["x"] + node["width"] + CLEARANCE
    bottom = node["y"] + node["height"] + CLEARANCE
    return _segment_through_box((a, b), (left, top, right, bottom))


def _compress(points):
    result = [points[0]]
    for point in points[1:]:
        if len(result) > 1 and (
            result[-2][0] == result[-1][0] == point[0] or result[-2][1] == result[-1][1] == point[1]
        ):
            result[-1] = point
        else:
            result.append(point)
    return result


def _format(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _path(points) -> str:
    chunks = [f"M{_format(points[0][0])} {_format(points[0][1])}"]
    for before, after in _segments(points):
        if before[0] == after[0]:
            chunks.append(f"V{_format(after[1])}")
        else:
            chunks.append(f"H{_format(after[0])}")
    return " ".join(chunks)


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, start)
    ratio = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared),
    )
    return math.dist(point, (start[0] + ratio * dx, start[1] + ratio * dy))


def _flatten_quadratic(start, control, end, tolerance=0.5, depth=0):
    if depth >= 9 or _point_segment_distance(control, start, end) <= tolerance:
        return [end]
    first = ((start[0] + control[0]) / 2, (start[1] + control[1]) / 2)
    second = ((control[0] + end[0]) / 2, (control[1] + end[1]) / 2)
    middle = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
    return _flatten_quadratic(start, first, middle, tolerance, depth + 1) + _flatten_quadratic(
        middle, second, end, tolerance, depth + 1
    )


def _flatten_cubic(start, first_control, second_control, end, tolerance=0.5, depth=0):
    if (
        depth >= 9
        or max(
            _point_segment_distance(first_control, start, end),
            _point_segment_distance(second_control, start, end),
        )
        <= tolerance
    ):
        return [end]
    first = (
        (start[0] + first_control[0]) / 2,
        (start[1] + first_control[1]) / 2,
    )
    middle_control = (
        (first_control[0] + second_control[0]) / 2,
        (first_control[1] + second_control[1]) / 2,
    )
    last = (
        (second_control[0] + end[0]) / 2,
        (second_control[1] + end[1]) / 2,
    )
    left_control = ((first[0] + middle_control[0]) / 2, (first[1] + middle_control[1]) / 2)
    right_control = (
        (middle_control[0] + last[0]) / 2,
        (middle_control[1] + last[1]) / 2,
    )
    middle = (
        (left_control[0] + right_control[0]) / 2,
        (left_control[1] + right_control[1]) / 2,
    )
    return _flatten_cubic(
        start, first, left_control, middle, tolerance, depth + 1
    ) + _flatten_cubic(middle, right_control, last, end, tolerance, depth + 1)


def _points_from_path(path: str):
    parsed = _parse_svg_path(path)
    if parsed is None:
        return None
    start, segments = parsed
    points = [start]
    x, y = start
    for segment in segments:
        values, command = segment["values"], segment["command"]
        end = segment["end"]
        if command == "Q":
            control = tuple(values[:2])
            points.extend(_flatten_quadratic((x, y), control, end))
        elif command == "C":
            points.extend(_flatten_cubic((x, y), tuple(values[:2]), tuple(values[2:4]), end))
        elif command == "H":
            points.append((values[0], y))
        elif command == "V":
            points.append((x, values[0]))
        else:
            points.append(end)
        x, y = end
    return points


def _segment_through_box(segment, box) -> bool:
    a, b = segment
    lower, upper = 0.0, 1.0
    for start, delta, minimum, maximum in (
        (a[0], b[0] - a[0], box[0], box[2]),
        (a[1], b[1] - a[1], box[1], box[3]),
    ):
        if abs(delta) < 1e-12:
            if not minimum < start < maximum:
                return False
            continue
        first, second = (minimum - start) / delta, (maximum - start) / delta
        lower = max(lower, min(first, second))
        upper = min(upper, max(first, second))
        if upper - lower <= 1e-9:
            return False
    return min(1.0, upper) - max(0.0, lower) > 1e-9


def _label(points, edge, diagram, placed, routed):
    content = edge["id"]
    width = max(38, min(edge.get("label_max_width", 220), len(content) * 7.1 + 16))
    segments = sorted(
        _segments(points),
        key=lambda pair: (
            -abs(pair[0][0] - pair[1][0]) - abs(pair[0][1] - pair[1][1]),
            pair[0][0] == pair[1][0],
        ),
    )
    for a, b in segments:
        for ratio in (0.5, 0.25, 0.75, 0.1, 0.9, 0.15, 0.85):
            base_x, base_y = a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio
            for offset in (0, -24, 24, -48, 48, -80, 80, -128, 128):
                x = base_x + (offset if a[0] == b[0] else 0)
                y = base_y + (offset if a[1] == b[1] else 0)
                box = (x - width / 2, y - 14, x + width / 2, y + 14)
                if (
                    box[0] < 4
                    or box[1] < 4
                    or box[2] > diagram["canvas"]["width"] - 4
                    or box[3] > diagram["canvas"]["height"] - 4
                ):
                    continue
                if any(
                    box[0] < node["x"] + node["width"]
                    and box[2] > node["x"]
                    and box[1] < node["y"] + node["height"]
                    and box[3] > node["y"]
                    for node in diagram["elements"]
                ):
                    continue
                if any(
                    box[0] < other[2] + 4
                    and box[2] + 4 > other[0]
                    and box[1] < other[3] + 4
                    and box[3] + 4 > other[1]
                    for other in placed
                ):
                    continue
                if any(
                    _segment_through_box(segment, box)
                    for other_id, route in routed.items()
                    if other_id != edge["id"]
                    for segment in _segments(route)
                ):
                    continue
                placed.append(box)
                return x, y, True
    a, b = segments[0]
    x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return x, y, False


def _route(start, end, obstacles, occupied, canvas, start_axis, end_axis):
    width, height = canvas["width"], canvas["height"]
    xs = {0.0, float(width), start[0], end[0]}
    ys = {0.0, float(height), start[1], end[1]}
    for node in obstacles:
        for value in (node["x"] - CLEARANCE, node["x"] + node["width"] + CLEARANCE):
            if 0 <= value <= width:
                xs.add(float(value))
        for value in (node["y"] - CLEARANCE, node["y"] + node["height"] + CLEARANCE):
            if 0 <= value <= height:
                ys.add(float(value))
    for segment in occupied:
        for point in segment:
            if 0 <= point[0] <= width:
                xs.add(point[0])
            if 0 <= point[1] <= height:
                ys.add(point[1])
            for delta in (-PARALLEL_GAP, PARALLEL_GAP):
                if 0 <= point[0] + delta <= width:
                    xs.add(point[0] + delta)
                if 0 <= point[1] + delta <= height:
                    ys.add(point[1] + delta)
    xx, yy = sorted(xs), sorted(ys)
    sx, sy = xx.index(start[0]), yy.index(start[1])
    ex, ey = xx.index(end[0]), yy.index(end[1])
    counter = count()
    initial = (sx, sy, 0)
    heap = [(0, 0.0, next(counter), initial)]
    best = {initial: (0, 0.0)}
    previous = {}
    blocked_cache = {}
    horizontal: dict[float, list[tuple[float, float]]] = defaultdict(list)
    vertical: dict[float, list[tuple[float, float]]] = defaultdict(list)
    diagonal = []
    for (ax, ay), (bx, by) in occupied:
        if ay == by:
            horizontal[ay].append((min(ax, bx), max(ax, bx)))
        elif ax == bx:
            vertical[ax].append((min(ay, by), max(ay, by)))
        else:
            diagonal.append(((ax, ay), (bx, by)))
    horizontal_keys, vertical_keys = sorted(horizontal), sorted(vertical)
    horizontal_obstacles = {}
    vertical_obstacles = {}

    def on_occupied(point):
        x, y = point
        return any(left < x < right for left, right in horizontal[y]) or any(
            top < y < bottom for top, bottom in vertical[x]
        )

    def diagonal_crossings(a, b):
        crossings = 0
        for prior in diagonal:
            point = _intersection_point((a, b), prior)
            if point is None:
                continue
            if any(math.dist(point, endpoint) < 0.001 for endpoint in (a, b, *prior)):
                return None
            crossings += 1
        return crossings

    def obstructed(a, b):
        if a[1] == b[1]:
            y, low, high = a[1], min(a[0], b[0]), max(a[0], b[0])
            if y not in horizontal_obstacles:
                horizontal_obstacles[y] = [
                    (node["x"] - CLEARANCE, node["x"] + node["width"] + CLEARANCE)
                    for node in obstacles
                    if node["y"] - CLEARANCE < y < node["y"] + node["height"] + CLEARANCE
                ]
            if any(max(low, left) < min(high, right) for left, right in horizontal_obstacles[y]):
                return None
            for lane_y in horizontal_keys[
                bisect_left(horizontal_keys, y - PARALLEL_GAP + 0.001) : bisect_right(
                    horizontal_keys, y + PARALLEL_GAP - 0.001
                )
            ]:
                if any(
                    max(low, left) < min(high, right) and (lane_y == y or right - left > LANE)
                    for left, right in horizontal[lane_y]
                ):
                    return None
            crossings = 0
            for x in vertical_keys[
                bisect_left(vertical_keys, low) : bisect_right(vertical_keys, high)
            ]:
                for top, bottom in vertical[x]:
                    if top <= y <= bottom:
                        if y == top or y == bottom:
                            return None
                        if low < x <= high:
                            crossings += 1
            extra = diagonal_crossings(a, b)
            return None if extra is None else crossings + extra
        x, low, high = a[0], min(a[1], b[1]), max(a[1], b[1])
        if x not in vertical_obstacles:
            vertical_obstacles[x] = [
                (node["y"] - CLEARANCE, node["y"] + node["height"] + CLEARANCE)
                for node in obstacles
                if node["x"] - CLEARANCE < x < node["x"] + node["width"] + CLEARANCE
            ]
        if any(max(low, top) < min(high, bottom) for top, bottom in vertical_obstacles[x]):
            return None
        for lane_x in vertical_keys[
            bisect_left(vertical_keys, x - PARALLEL_GAP + 0.001) : bisect_right(
                vertical_keys, x + PARALLEL_GAP - 0.001
            )
        ]:
            if any(
                max(low, top) < min(high, bottom) and (lane_x == x or bottom - top > LANE)
                for top, bottom in vertical[lane_x]
            ):
                return None
        crossings = 0
        for y in horizontal_keys[
            bisect_left(horizontal_keys, low) : bisect_right(horizontal_keys, high)
        ]:
            for left, right in horizontal[y]:
                if left <= x <= right:
                    if x == left or x == right:
                        return None
                    if low < y <= high:
                        crossings += 1
        extra = diagonal_crossings(a, b)
        return None if extra is None else crossings + extra

    while heap:
        bends, distance, _, state = heappop(heap)
        if (bends, distance) != best[state]:
            continue
        ix, iy, direction = state
        if ix == ex and iy == ey and direction == end_axis:
            path = [(xx[ix], yy[iy])]
            while state in previous:
                state = previous[state]
                path.append((xx[state[0]], yy[state[1]]))
            path.reverse()
            return _compress(path)
        for step_x, step_y, next_direction in ((1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2)):
            if direction == 0 and next_direction != start_axis:
                continue
            if direction and direction != next_direction and on_occupied((xx[ix], yy[iy])):
                continue
            nx, ny = ix + step_x, iy + step_y
            if nx < 0 or nx >= len(xx) or ny < 0 or ny >= len(yy):
                continue
            a, b = (xx[ix], yy[iy]), (xx[nx], yy[ny])
            key = (min(ix, nx), min(iy, ny), next_direction)
            if key not in blocked_cache:
                blocked_cache[key] = obstructed(a, b)
            crossings = blocked_cache[key]
            if crossings is None:
                continue
            new_cost = (
                bends + int(direction != 0 and direction != next_direction),
                distance + abs(a[0] - b[0]) + abs(a[1] - b[1]) + CROSSING_COST * crossings,
            )
            next_state = (nx, ny, next_direction)
            if new_cost < best.get(next_state, (float("inf"), float("inf"))):
                best[next_state] = new_cost
                previous[next_state] = state
                heappush(heap, (*new_cost, next(counter), next_state))
    return None


def _ports_for(nodes, relations):
    ports: dict[str, list[tuple[tuple[str, str], str]]] = {}
    preferred: dict[tuple[str, str], str] = {}
    assigned = {}
    for edge in relations:
        for endpoint, opposite in (("from", "to"), ("to", "from")):
            node = nodes[edge[endpoint]]
            key = (edge["id"], endpoint)
            anchor = edge.get(f"{endpoint}_anchor")
            if isinstance(anchor, dict) and anchor.get("side") != "auto":
                side = anchor["side"]
                preferred[key] = side
                assigned[key] = {side: _anchor_point(node, anchor)}
                continue
            side = _side(node, nodes[edge[opposite]])
            preferred[key] = side
            ports.setdefault(node["id"], []).append((key, edge[opposite]))
    for node_id, entries in ports.items():
        for side in ("left", "right", "top", "bottom"):
            axis = 1 if side in {"left", "right"} else 0
            ordered_entries = sorted(
                entries, key=lambda entry: (_center(nodes[entry[1]])[axis], entry[0])
            )
            for index, (key, _) in enumerate(ordered_entries):
                assigned.setdefault(key, {})[side] = _port(
                    nodes[node_id], side, index, len(entries)
                )
    return assigned, preferred


def _side_options(edge: dict, endpoint: str, preferred: str) -> list[str]:
    anchor = edge.get(f"{endpoint}_anchor") or {}
    if anchor.get("side") in {"left", "right", "top", "bottom"}:
        return [preferred]
    return [
        preferred,
        *[side for side in ("left", "right", "top", "bottom") if side != preferred],
    ]


def _manual_routes_clear(diagram: dict) -> bool:
    for edge in diagram["relations"]:
        if edge.get("route_mode", "auto") != "manual":
            continue
        points = _points_from_path(edge["path"])
        if points is None:
            return False
        for node in diagram["elements"]:
            if node["id"] in {edge["from"], edge["to"]}:
                continue
            box = (
                node["x"] - CLEARANCE,
                node["y"] - CLEARANCE,
                node["x"] + node["width"] + CLEARANCE,
                node["y"] + node["height"] + CLEARANCE,
            )
            if any(_segment_through_box(segment, box) for segment in _segments(points)):
                return False
    return True


def _route_incremental(
    diagram, previous, changed_node, changed_relation, assigned, preferred
) -> bool:
    old_edges = {edge["id"]: edge for edge in previous["relations"]}
    old_points = {ident: _points_from_path(edge["path"]) for ident, edge in old_edges.items()}
    if any(points is None for points in old_points.values()) or route_conflicts(previous):
        return False
    nodes = {node["id"]: node for node in diagram["elements"]}
    affected = {changed_relation} if changed_relation else set()
    if changed_node:
        moved = nodes[changed_node]
        for edge in diagram["relations"]:
            if changed_node in {edge["from"], edge["to"]}:
                affected.add(edge["id"])
            elif edge["id"] in old_points and any(
                _blocked_by_node(a, b, moved) for a, b in _segments(old_points[edge["id"]])
            ):
                affected.add(edge["id"])
    if not affected:
        return False
    routed = {
        edge["id"]: old_points[edge["id"]]
        for edge in diagram["relations"]
        if edge["id"] not in affected and edge["id"] in old_points
    }
    if len(routed) + len(affected) != len(diagram["relations"]):
        return False
    occupied = [segment for points in routed.values() for segment in _segments(points)]
    for edge in (edge for edge in diagram["relations"] if edge["id"] in affected):
        if edge.get("route_mode", "auto") == "manual":
            points = _points_from_path(edge["path"])
            if points is None:
                return False
            routed[edge["id"]] = points
            occupied.extend(_segments(points))
            continue
        source, target = nodes[edge["from"]], nodes[edge["to"]]
        excluded = {source.get("parent"), target.get("parent")}
        if source.get("parent") == target["id"]:
            excluded.add(target["id"])
        if target.get("parent") == source["id"]:
            excluded.add(source["id"])
        obstacles = [node for node in nodes.values() if node["id"] not in excluded]
        source_key, target_key = (edge["id"], "from"), (edge["id"], "to")
        source_sides = _side_options(edge, "from", preferred[source_key])
        target_sides = _side_options(edge, "to", preferred[target_key])
        points = None
        for start_side in source_sides:
            if points:
                break
            for end_side in target_sides:
                start_port, end_port = (
                    assigned[source_key][start_side],
                    assigned[target_key][end_side],
                )
                start, end = _outside(start_port, start_side), _outside(end_port, end_side)
                if any(
                    _intersects(stub, prior)
                    for stub in ((start_port, start), (end, end_port))
                    for prior in occupied
                ):
                    continue
                core = _route(
                    start,
                    end,
                    obstacles,
                    occupied,
                    diagram["canvas"],
                    1 if start_side in {"left", "right"} else 2,
                    1 if end_side in {"left", "right"} else 2,
                )
                if core:
                    points = _compress([start_port, *core, end_port])
                    break
        if not points:
            return False
        routed[edge["id"]] = points
        occupied.extend(_segments(points))
    placed = []
    for edge in diagram["relations"]:
        points = routed[edge["id"]]
        if edge.get("route_mode", "auto") != "manual":
            edge["path"] = _path(points)
        if edge.get("label_mode", "auto") != "manual":
            edge["label_x"], edge["label_y"], visible = _label(
                points, edge, diagram, placed, routed
            )
            edge["auto_hidden_label"] = not visible
    return not route_conflicts(diagram) and _manual_routes_clear(diagram)


def route_diagram(
    diagram: dict,
    *,
    previous: dict | None = None,
    changed_node: str | None = None,
    changed_relation: str | None = None,
) -> dict:
    """Update every SVG path in-place, or raise without returning a partial layout."""
    nodes = {node["id"]: node for node in diagram["elements"]}
    relations = diagram["relations"]
    assigned, preferred = _ports_for(nodes, relations)
    for edge in relations:
        if edge.get("route_mode", "auto") != "manual":
            continue
        source = assigned[(edge["id"], "from")][preferred[(edge["id"], "from")]]
        target = assigned[(edge["id"], "to")][preferred[(edge["id"], "to")]]
        edge["path"] = _reanchor_path(edge["path"], source, target)
    if previous is not None and _route_incremental(
        diagram, previous, changed_node, changed_relation, assigned, preferred
    ):
        return diagram
    # A failed relation gets priority in the next pass. Rebuilding from scratch
    # prevents early local routes from permanently enclosing a later trunk.
    order = [
        *[edge for edge in relations if edge.get("route_mode", "auto") == "manual"],
        *[edge for edge in relations if edge.get("route_mode", "auto") != "manual"],
    ]
    seen_orders = set()
    last_failure = relations[0]["id"] if relations else ""
    for _attempt in range(min(3, max(1, len(relations) * 2))):
        signature = tuple(edge["id"] for edge in order)
        if signature in seen_orders:
            break
        seen_orders.add(signature)
        occupied = []
        routed = {}
        for edge in order:
            source, target = nodes[edge["from"]], nodes[edge["to"]]
            if edge.get("route_mode", "auto") == "manual":
                points = _points_from_path(edge["path"])
                if points is None:
                    last_failure = edge["id"]
                    order.remove(edge)
                    order.insert(0, edge)
                    break
                routed[edge["id"]] = points
                occupied.extend(_segments(points))
                continue
            excluded = {source.get("parent"), target.get("parent")}
            if source.get("parent") == target["id"]:
                excluded.add(target["id"])
            if target.get("parent") == source["id"]:
                excluded.add(source["id"])
            obstacles = [node for node in nodes.values() if node["id"] not in excluded]
            source_key, target_key = (edge["id"], "from"), (edge["id"], "to")
            source_sides = _side_options(edge, "from", preferred[source_key])
            target_sides = _side_options(edge, "to", preferred[target_key])
            points = None
            for start_side in source_sides:
                if points:
                    break
                for end_side in target_sides:
                    start_port, end_port = (
                        assigned[source_key][start_side],
                        assigned[target_key][end_side],
                    )
                    start, end = _outside(start_port, start_side), _outside(end_port, end_side)
                    if any(
                        _intersects(segment, prior)
                        for segment in ((start_port, start), (end, end_port))
                        for prior in occupied
                    ):
                        continue
                    core = _route(
                        start,
                        end,
                        obstacles,
                        occupied,
                        diagram["canvas"],
                        1 if start_side in {"left", "right"} else 2,
                        1 if end_side in {"left", "right"} else 2,
                    )
                    if core:
                        points = _compress([start_port, *core, end_port])
                        break
            if not points:
                last_failure = edge["id"]
                order.remove(edge)
                order.insert(0, edge)
                break
            occupied.extend(_segments(points))
            routed[edge["id"]] = points
        else:
            placed = []
            for edge in relations:
                points = routed[edge["id"]]
                if edge.get("route_mode", "auto") != "manual":
                    edge["path"] = _path(points)
                if edge.get("label_mode", "auto") != "manual":
                    edge["label_x"], edge["label_y"], visible = _label(
                        points, edge, diagram, placed, routed
                    )
                    edge["auto_hidden_label"] = not visible
            if route_conflicts(diagram):
                raise RouteUnavailable(route_conflicts(diagram)[0][1])
            if not _manual_routes_clear(diagram):
                manual = next(
                    edge["id"] for edge in relations if edge.get("route_mode") == "manual"
                )
                raise RouteUnavailable(manual)
            return diagram
    raise RouteUnavailable(last_failure)


def route_intersections(diagram: dict) -> list[tuple[str, str]]:
    """List geometrically intersecting edge pairs, including allowed crossings."""
    all_segments = []
    issues = []
    for edge in diagram["relations"]:
        for segment in _segments(_points_from_path(edge["path"]) or []):
            for other_id, other_segment in all_segments:
                if other_id != edge["id"] and _intersects(segment, other_segment):
                    issues.append((other_id, edge["id"]))
            all_segments.append((edge["id"], segment))
    return issues


def route_conflicts(diagram: dict) -> list[tuple[str, str]]:
    """Report overlaps, close free parallel runs, and crossings without room for a bridge."""
    prior = []
    issues = []
    vertices = {edge["id"]: _route_vertices(edge["path"]) for edge in diagram["relations"]}
    for edge in diagram["relations"]:
        for segment in _segments(_points_from_path(edge["path"]) or []):
            for other_id, other in prior:
                if other_id == edge["id"]:
                    continue
                if _collinear_overlap(segment, other):
                    issues.append((other_id, edge["id"]))
                    continue
                gap = _parallel_gap(segment, other)
                if gap is not None:
                    if gap == 0 or (
                        gap < PARALLEL_GAP
                        and min(
                            abs(segment[1][0] - segment[0][0]) + abs(segment[1][1] - segment[0][1]),
                            abs(other[1][0] - other[0][0]) + abs(other[1][1] - other[0][1]),
                        )
                        > LANE
                    ):
                        issues.append((other_id, edge["id"]))
                elif (crossing := _intersection_point(segment, other)) is not None:
                    all_vertices = vertices[edge["id"]] | vertices[other_id]
                    if any(math.dist(crossing, point) < 1e-3 for point in all_vertices):
                        issues.append((other_id, edge["id"]))
            prior.append((edge["id"], segment))
    return issues
