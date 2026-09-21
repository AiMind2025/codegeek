"""
决策核心 — 分阶段调度白天/夜晚行为
"""
from typing import Any

from .grid import next_step
from .protocol import (
    PIONEER,
    ROBOT_THREAT_PRIORITY,
    STATION,
    TOWER_TYPES,
    WALL,
    WALL_MATERIAL,
    WEAPON_BUILD_COST,
    WORKER,
    Pos,
    Robot,
    Turn,
    Unit,
    attack_command,
    build_command,
    buy_command,
    chebyshev,
    collect_command,
    move_command,
    sell_command,
    station_footprint,
    use_command,
)

TOWER_LOADOUT = ("gatling", "railgun", "rocket")
STONE_BATCH = 10
_NEIGHBOUR_STEPS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
)


def decide(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        turn = Turn.load(payload)
        commands: dict[int, dict[str, Any]] = {}
        if turn.is_day:
            _day(turn, commands)
        else:
            _night(turn, commands)
    except Exception:
        commands = {}
    return {
        "roleCommandMap": {str(k): v for k, v in commands.items()},
        "prompt": "",
        "executeCmd": "",
    }


def _day(turn: Turn, commands: dict[int, dict[str, Any]]) -> None:
    tower_sites = _tower_sites(turn)
    wall_order = _wall_order(turn)
    standing_towers = {u.pos for u in turn.weapons()}
    standing_walls = {u.pos for u in turn.walls()}
    occupied = turn.occupied_cells()
    towers_missing = [p for p in tower_sites if p not in standing_towers]
    walls_missing = [p for p in wall_order if p not in standing_walls]
    free_towers = [p for p in towers_missing if p not in occupied]
    free_walls = [p for p in walls_missing if p not in occupied]

    claimed: set[Pos] = set()
    workers = turn.workers()
    day = turn.day_number

    if day <= 2:
        for w in workers:
            _worker_build_tower(turn, w, tower_sites, free_towers, claimed, commands)
    else:
        if len(workers) >= 1:
            _worker_miner(turn, workers[0], free_towers, free_walls, claimed, commands)
        if len(workers) >= 2:
            _worker_flex(turn, workers[1], free_walls, claimed, commands)

    pioneer = _find_pioneer(turn)
    if pioneer is not None:
        _pioneer_day(turn, pioneer, claimed, commands)


def _worker_build_tower(turn, role, sites, free_towers, claimed, commands):
    for idx, site in enumerate(sites):
        if site in free_towers and site not in claimed:
            tower_name = TOWER_LOADOUT[idx % len(TOWER_LOADOUT)]
            _build_or_walk(turn, role, site, tower_name, claimed, commands)
            return
    _mine_stone(turn, role, claimed, commands)


def _worker_miner(turn, role, free_towers, free_walls, claimed, commands):
    if free_towers and turn.gold >= WEAPON_BUILD_COST:
        for idx, site in enumerate(_tower_sites(turn)):
            if site in free_towers and site not in claimed:
                tower_name = TOWER_LOADOUT[idx % len(TOWER_LOADOUT)]
                _build_or_walk(turn, role, site, tower_name, claimed, commands)
                return
    if role.backpack_almost_full:
        _go_sell(turn, role, claimed, commands)
        return
    stones = role.backpack.count(WALL_MATERIAL)
    if stones < STONE_BATCH:
        _mine_stone(turn, role, claimed, commands)
        return
    iron = role.backpack.count("iron")
    copper = role.backpack.count("copper")
    if iron < 5:
        _mine_ore(turn, role, "iron", claimed, commands)
        return
    if copper < 5:
        _mine_ore(turn, role, "copper", claimed, commands)
        return
    _mine_stone(turn, role, claimed, commands)


def _worker_flex(turn, role, free_walls, claimed, commands):
    if role.backpack:
        _go_sell(turn, role, claimed, commands)
        return
    if turn.gold >= 100 and turn.day_number >= 3:
        _go_buy_upgrade(turn, role, claimed, commands)
        return
    stones = role.backpack.count(WALL_MATERIAL)
    if stones > 0 and free_walls:
        for site in free_walls:
            if site not in claimed:
                _build_or_walk(turn, role, site, WALL, claimed, commands)
                return
    _mine_stone(turn, role, claimed, commands)


def _pioneer_day(turn, role, claimed, commands):
    shop = turn.weapon_shop_pos()
    if shop is not None and chebyshev(role.pos, shop) > 4:
        step = _step_toward(turn, role, shop, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)


def _night(turn, commands):
    claimed: set[Pos] = set()
    enemy_robots = tuple(
        r for r in turn.robots
        if r.target_team == turn.team_type and not r.is_dizzy
    )
    pairs = list(zip(turn.controllable(), turn.weapons()))
    for role, tower in pairs:
        dist = chebyshev(role.pos, tower.pos)
        if dist <= 1:
            if tower.cooldown > 0:
                continue
            targets = _select_attack_targets(tower, enemy_robots, turn)
            if targets:
                commands[tower.unit_id] = attack_command(role.unit_id, targets)
        else:
            step = _step_toward(turn, role, tower.pos, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)

    used_roles = {r.unit_id for r, _ in pairs}
    for role in turn.controllable():
        if role.unit_id in used_roles:
            continue
        nearest = min(turn.weapons(), key=lambda t: chebyshev(role.pos, t.pos))
        if chebyshev(role.pos, nearest.pos) <= 1:
            continue
        step = _step_toward(turn, role, nearest.pos, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)


def _select_attack_targets(tower, robots, turn):
    reach = tower.range_of_attack()
    base = turn.station()
    base_pos = base.pos if base else Pos(20, 16)
    in_range = [
        r for r in robots
        if r.health > 0 and chebyshev(tower.pos, r.pos) <= reach
    ]
    if not in_range:
        return []
    in_range.sort(key=lambda r: (-r.threat_score, chebyshev(r.pos, base_pos), r.health))
    if tower.kind == "railgun":
        return [_best_railgun_target(tower, in_range)]
    n_targets = tower.multi_target_count()
    if tower.kind == "gatling":
        return _gatling_targets(tower, in_range, n_targets)
    if tower.kind == "rocket":
        return _rocket_targets(tower, in_range, n_targets)
    return [in_range[0].pos]


def _best_railgun_target(tower, robots):
    best_pos = robots[0].pos
    best_damage = 0
    for candidate in robots:
        damage = 0
        energy = tower.attackPower
        for r in sorted(robots, key=lambda x: chebyshev(tower.pos, x.pos)):
            if chebyshev(tower.pos, r.pos) > tower.range_of_attack():
                break
            if _on_line(tower.pos, candidate.pos, r.pos):
                dmg = min(energy, r.health)
                damage += dmg
                energy -= dmg
                if energy <= 0:
                    break
        if damage > best_damage:
            best_damage = damage
            best_pos = candidate.pos
    return best_pos


def _gatling_targets(tower, robots, n):
    if len(robots) <= n:
        return [r.pos for r in robots]
    best = robots[:n]
    for i, anchor in enumerate(robots):
        cone = [anchor]
        for j, other in enumerate(robots):
            if i == j:
                continue
            if _within_90deg(tower.pos, anchor.pos, other.pos):
                cone.append(other)
            if len(cone) >= n:
                break
        if len(cone) > len(best):
            best = cone[:n]
    return [r.pos for r in best]


def _rocket_targets(tower, robots, n):
    targets = []
    used_areas = set()
    for r in robots:
        if len(targets) >= n:
            break
        area = (r.pos.x // 3, r.pos.y // 3)
        if area in used_areas:
            continue
        targets.append(r.pos)
        used_areas.add(area)
    return targets


def _find_pioneer(turn):
    for u in turn.ours:
        if u.kind == PIONEER and u.health > 0:
            return u
    return None


def _mine_stone(turn, role, claimed, commands):
    _mine_ore(turn, role, "stone", claimed, commands)


def _mine_ore(turn, role, ore_type, claimed, commands):
    if role.backpack_full:
        return
    mines_map = {
        "stone": turn.stone_mines(),
        "iron": turn.iron_mines(),
        "copper": turn.copper_mines(),
    }
    mines = mines_map.get(ore_type, ())
    candidates = sorted(
        (m for m in mines if m not in claimed),
        key=lambda p: (chebyshev(role.pos, p), p.x, p.y),
    )
    for mine in candidates:
        if role.pos != mine and chebyshev(role.pos, mine) <= 1:
            commands[role.unit_id] = collect_command(mine)
            claimed.add(mine)
            return
        step = _step_toward(turn, role, mine, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return


def _go_sell(turn, role, claimed, commands):
    vendor = turn.vendor_pos()
    if vendor is None:
        return
    if chebyshev(role.pos, vendor) <= 1:
        prices = turn.vendor_prices
        items = sorted(set(role.backpack), key=lambda n: prices.get(n, 0), reverse=True)
        if items:
            for item in items:
                count = role.backpack.count(item)
                if count > 0:
                    commands[role.unit_id] = sell_command(item, count)
                    return
    else:
        step = _step_toward(turn, role, vendor, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return
        _mine_stone(turn, role, claimed, commands)


def _go_buy_upgrade(turn, role, claimed, commands):
    shop = turn.weapon_shop_pos()
    if shop is None:
        return
    if chebyshev(role.pos, shop) <= 1:
        _buy_priority(turn, role, commands)
    else:
        step = _step_toward(turn, role, shop, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return
        _mine_stone(turn, role, claimed, commands)


def _buy_priority(turn, role, commands):
    if role.backpack_full:
        return
    gold = turn.gold
    weapons = turn.weapons()
    for w in weapons:
        if w.level < 3:
            voucher = "WeaponUpgradeVoucher1" if w.level == 1 else "WeaponUpgradeVoucher2"
            price = 100 if w.level == 1 else 150
            if gold >= price and voucher not in role.backpack:
                commands[role.unit_id] = buy_command(voucher, 1)
                return
    station = turn.station()
    if station and station.level < 3:
        voucher = "StationUpgradeVoucher1" if station.level == 1 else "StationUpgradeVoucher2"
        price = 100 if station.level == 1 else 150
        if gold >= price and voucher not in role.backpack:
            commands[role.unit_id] = buy_command(voucher, 1)
            return
    if not turn.is_day and turn.rounds_remaining_today > 50:
        if gold >= 100 and "DizzyWeapon" not in role.backpack:
            commands[role.unit_id] = buy_command("DizzyWeapon", 1)
            return
        if gold >= 100 and "Bomb" not in role.backpack:
            commands[role.unit_id] = buy_command("Bomb", 1)
            return
    walls = turn.walls()
    low_walls = [w for w in walls if w.health < 800]
    if low_walls and gold >= 10 and "WallFixer" not in role.backpack:
        commands[role.unit_id] = buy_command("WallFixer", min(len(low_walls), gold // 10))
        return
    low_hp_roles = [u for u in turn.ours if u.kind in (WORKER, PIONEER) and u.health < 110]
    if low_hp_roles and gold >= 10 and "Medicine" not in role.backpack:
        commands[role.unit_id] = buy_command("Medicine", 1)
        return


def _build_or_walk(turn, role, target, name, claimed, commands):
    if role.pos != target and chebyshev(role.pos, target) <= 1:
        commands[role.unit_id] = build_command(target, name)
        claimed.add(target)
        return
    step = _step_toward(turn, role, target, claimed)
    if step is not None:
        commands[role.unit_id] = move_command(step)


def _step_toward(turn, role, target, claimed):
    for pos in _neighbours(role.pos):
        if pos == target:
            if turn.land(pos) and pos not in turn.blocked(role) and pos not in claimed:
                claimed.add(pos)
                return pos
    step = next_step(turn, role, target)
    if step is not None and step not in claimed:
        claimed.add(step)
        return step
    return None


def _tower_sites(turn):
    station = turn.station()
    if station is None:
        return []
    footprint = station_footprint(station.pos)
    cells = [pos for pos in _cells_at_distance(station.pos, 1) if turn.land(pos)]
    cells.sort(key=lambda pos: (_footprint_distance(pos, footprint), pos.x, pos.y))
    return cells[:3]


def _wall_order(turn):
    station = turn.station()
    if station is None:
        return []
    footprint = station_footprint(station.pos)
    xs = [pos.x for pos in footprint]
    ys = [pos.y for pos in footprint]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    order = [
        *(Pos(x, ymin - 2) for x in range(xmax + 2, xmin - 3, -1)),
        *(Pos(xmin - 2, y) for y in range(ymin - 1, ymax + 2)),
        *(Pos(x, ymax + 2) for x in range(xmin - 2, xmax + 3)),
        *(Pos(xmax + 2, y) for y in range(ymax + 1, ymin - 2, -1)),
    ]
    entrance = Pos(xmax + 2, ymin - 1)
    return [pos for pos in order if pos != entrance and turn.land(pos)]


def _cells_at_distance(station_pos, radius):
    footprint = station_footprint(station_pos)
    xs = [pos.x for pos in footprint]
    ys = [pos.y for pos in footprint]
    cells = []
    for x in range(min(xs) - radius, max(xs) + radius + 1):
        for y in range(min(ys) - radius, max(ys) + radius + 1):
            pos = Pos(x, y)
            if pos in footprint:
                continue
            if _footprint_distance(pos, footprint) == radius:
                cells.append(pos)
    return cells


def _footprint_distance(pos, footprint):
    if not footprint:
        return 0
    return min(chebyshev(pos, cell) for cell in footprint)


def _neighbours(pos):
    return [Pos(pos.x + dx, pos.y + dy) for dx, dy in _NEIGHBOUR_STEPS]


def _sign(v):
    return (v > 0) - (v < 0)


def _on_line(start, end, point):
    dx = abs(end.x - start.x)
    dy = abs(end.y - start.y)
    sx = _sign(end.x - start.x)
    sy = _sign(end.y - start.y)
    x, y = start.x, start.y
    err = dx - dy
    while True:
        if x == point.x and y == point.y:
            return True
        if x == end.x and y == end.y:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return False


def _within_90deg(center, a, b):
    dax = a.x - center.x
    day = a.y - center.y
    dbx = b.x - center.x
    dby = b.y - center.y
    dot = dax * dbx + day * dby
    return dot >= 0
