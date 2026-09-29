"""Deterministic orthogonal routing for the manager structure diagram.

Routes are calculated from semantic endpoints. A failed layout is never saved.
The legacy SVG path remains a portable rendering field, not an input to this router.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from heapq import heappop, heappush
from itertools import count

CLEARANCE = 2
LANE = 4
PARALLEL_GAP = 10
CROSSING_COST = 24


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


def _intersects(first, second) -> bool:
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second
    if ay == by and cx == dx:  # perpendicular
        return min(ax, bx) <= cx <= max(ax, bx) and min(cy, dy) <= ay <= max(cy, dy)
    if ax == bx and cx != dx:
        return min(cx, dx) <= ax <= max(cx, dx) and min(ay, by) <= cy <= max(ay, by)
    if ay == by and cy == dy:
        return ay == cy and max(min(ax, bx), min(cx, dx)) <= min(max(ax, bx), max(cx, dx))
    if ax == bx and cx == dx:
        return ax == cx and max(min(ay, by), min(cy, dy)) <= min(max(ay, by), max(cy, dy))
    return False


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
    if a[0] == b[0]:
        return left < a[0] < right and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom)
    return top < a[1] < bottom and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right)


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


def _points_from_path(path: str):
    if not re.fullmatch(r"[MHV0-9. +\-]+", path):
        return None
    tokens = re.findall(r"[MHV]|-?\d+(?:\.\d+)?", path)
    points = []
    index = 0
    x = y = 0.0
    while index < len(tokens):
        command = tokens[index]
        if command == "M":
            if index + 2 >= len(tokens):
                return None
            x, y = float(tokens[index + 1]), float(tokens[index + 2])
            index += 3
        elif command == "H":
            if index + 1 >= len(tokens):
                return None
            x = float(tokens[index + 1])
            index += 2
        elif command == "V":
            if index + 1 >= len(tokens):
                return None
            y = float(tokens[index + 1])
            index += 2
        else:
            return None
        points.append((x, y))
    return points if len(points) >= 2 else None


def _segment_through_box(segment, box) -> bool:
    a, b = segment
    if a[0] == b[0]:
        return box[0] < a[0] < box[2] and max(min(a[1], b[1]), box[1]) < min(
            max(a[1], b[1]), box[3]
        )
    return box[1] < a[1] < box[3] and max(min(a[0], b[0]), box[0]) < min(max(a[0], b[0]), box[2])


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
    for (ax, ay), (bx, by) in occupied:
        if ay == by:
            horizontal[ay].append((min(ax, bx), max(ax, bx)))
        else:
            vertical[ax].append((min(ay, by), max(ay, by)))
    horizontal_keys, vertical_keys = sorted(horizontal), sorted(vertical)
    horizontal_obstacles = {}
    vertical_obstacles = {}

    def on_occupied(point):
        x, y = point
        return any(left < x < right for left, right in horizontal[y]) or any(
            top < y < bottom for top, bottom in vertical[x]
        )

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
            return crossings
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
        return crossings

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
    for edge in relations:
        for endpoint, opposite in (("from", "to"), ("to", "from")):
            node = nodes[edge[endpoint]]
            side = _side(node, nodes[edge[opposite]])
            key = (edge["id"], endpoint)
            preferred[key] = side
            ports.setdefault(node["id"], []).append((key, edge[opposite]))
    assigned = {}
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
        source, target = nodes[edge["from"]], nodes[edge["to"]]
        excluded = {source.get("parent"), target.get("parent")}
        if source.get("parent") == target["id"]:
            excluded.add(target["id"])
        if target.get("parent") == source["id"]:
            excluded.add(source["id"])
        obstacles = [node for node in nodes.values() if node["id"] not in excluded]
        source_key, target_key = (edge["id"], "from"), (edge["id"], "to")
        source_sides = [
            preferred[source_key],
            *[s for s in ("left", "right", "top", "bottom") if s != preferred[source_key]],
        ]
        target_sides = [
            preferred[target_key],
            *[s for s in ("left", "right", "top", "bottom") if s != preferred[target_key]],
        ]
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
        edge["path"] = _path(points)
        edge["label_x"], edge["label_y"], visible = _label(points, edge, diagram, placed, routed)
        edge["auto_hidden_label"] = not visible
    return not route_conflicts(diagram)


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
    if previous is not None and _route_incremental(
        diagram, previous, changed_node, changed_relation, assigned, preferred
    ):
        return diagram
    # A failed relation gets priority in the next pass. Rebuilding from scratch
    # prevents early local routes from permanently enclosing a later trunk.
    order = list(relations)
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
            excluded = {source.get("parent"), target.get("parent")}
            if source.get("parent") == target["id"]:
                excluded.add(target["id"])
            if target.get("parent") == source["id"]:
                excluded.add(source["id"])
            obstacles = [node for node in nodes.values() if node["id"] not in excluded]
            source_key, target_key = (edge["id"], "from"), (edge["id"], "to")
            source_sides = [
                preferred[source_key],
                *[
                    side
                    for side in ("left", "right", "top", "bottom")
                    if side != preferred[source_key]
                ],
            ]
            target_sides = [
                preferred[target_key],
                *[
                    side
                    for side in ("left", "right", "top", "bottom")
                    if side != preferred[target_key]
                ],
            ]
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
                edge["path"] = _path(points)
                edge["label_x"], edge["label_y"], visible = _label(
                    points, edge, diagram, placed, routed
                )
                edge["auto_hidden_label"] = not visible
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
    for edge in diagram["relations"]:
        for segment in _segments(_points_from_path(edge["path"]) or []):
            for other_id, other in prior:
                if other_id == edge["id"]:
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
                elif _intersects(segment, other):
                    (ax, ay), (bx, by) = segment
                    (cx, cy), (dx, dy) = other
                    crossing = (cx, ay) if ay == by else (ax, cy)
                    if crossing in segment or crossing in other:
                        issues.append((other_id, edge["id"]))
            prior.append((edge["id"], segment))
    return issues
