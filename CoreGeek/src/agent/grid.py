from heapq import heappop, heappush
from itertools import count

from .protocol import Pos, Turn, Unit, chebyshev

_STEPS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
)


def next_step(turn, moving, goal):
    blocked = turn.blocked(moving)
    order = count()
    frontier = [
        (chebyshev(moving.pos, goal), 0, next(order), moving.pos)
    ]
    came_from = {}
    best = {moving.pos: 0}
    seen = set()

    while frontier:
        _, cost, _, current = heappop(frontier)
        if current in seen:
            continue
        seen.add(current)  # ← 关键修复：标记已处理
        if current == goal:
            return _first_step(came_from, moving.pos, goal)
        for dx, dy in _STEPS:
            step = Pos(current.x + dx, current.y + dy)
            # 允许走到 goal（即使它是 zone/建筑），其他位置必须可通行
            if step != goal and (step in blocked or not turn.land(step)):
                continue
            new_cost = cost + 1
            if new_cost >= best.get(step, new_cost + 1):
                continue
            best[step] = new_cost
            came_from[step] = current
            heappush(
                frontier,
                (new_cost + chebyshev(step, goal), new_cost, next(order), step),
            )
    return None


def _first_step(came_from, start, goal):
    current = goal
    while came_from[current] != start:
        current = came_from[current]
    return current
