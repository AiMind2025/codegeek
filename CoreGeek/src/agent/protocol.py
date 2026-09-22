from dataclasses import dataclass
from typing import Any

DAY_ROUNDS = 70
NIGHT_ROUNDS = 60
ROUNDS_PER_DAY = DAY_ROUNDS + NIGHT_ROUNDS
MAX_ROUNDS = 1300

WEAPON_BUILD_COST = 25
WALL_MATERIAL = "stone"
LAND = "land"
STATION = "station"
WALL = "wall"
WORKER = "worker"
PIONEER = "pioneer"
TOWER_TYPES = ("gatling", "railgun", "rocket")
CONTROLLABLE_TYPES = (WORKER, PIONEER)

TOWER_RANGE_BY_LEVEL = {
    "gatling": (3, 5, 7),
    "railgun": (6, 8, 10),
    "rocket": (10, 15, 10**9),
}

TOWER_MULTI_TARGET = {
    "gatling": (1, 2, 3),
    "rocket": (1, 2, 3),
    "railgun": (1, 1, 1),
}

ROCKET_COOLDOWN = 3

ROBOT_STATS = {
    "smallRobot":  {"hp": 40,  "atk": 5,  "range": 3, "score": 1},
    "middleRobot": {"hp": 60,  "atk": 10, "range": 3, "score": 2},
    "largeRobot":  {"hp": 500, "atk": 20, "range": 3, "score": 4},
    "bossRobot":   {"hp": 800, "atk": 40, "range": 3, "score": 10},
}

ROBOT_THREAT_PRIORITY = {
    "bossRobot":   4,
    "largeRobot":  3,
    "middleRobot": 2,
    "smallRobot":  1,
}

UPGRADE_PRICES = {
    "WeaponUpgradeVoucher1":  100,
    "WeaponUpgradeVoucher2":  150,
    "WallUpgradeVoucher1":    20,
    "WallUpgradeVoucher2":    30,
    "StationUpgradeVoucher1": 100,
    "StationUpgradeVoucher2": 150,
}

CONSUMABLE_PRICES = {
    "WallFixer":               10,
    "Medicine":                10,
    "DizzyWeapon":            100,
    "Bomb":                   100,
    "SmallRobotSummonOrder":   20,
    "MiddleRobotSummonOrder":  30,
    "LargeRobotSummonOrder":  100,
    "BossRobotSummonOrder":   200,
}

TASK_ITEM_NAMES = {
    "AcientTablet", "StarSand", "FlameBreath",
    "FrostPotion", "ThornAmulet", "IronWhistle",
}


@dataclass(frozen=True)
class Pos:
    x: int
    y: int

    @classmethod
    def load(cls, raw):
        return cls(int(raw["x"]), int(raw["y"]))

    def dump(self):
        return {"x": self.x, "y": self.y}

    def __repr__(self):
        return f"({self.x},{self.y})"


def chebyshev(a, b):
    return max(abs(a.x - b.x), abs(a.y - b.y))


def station_footprint(pos):
    return (
        pos,
        Pos(pos.x + 1, pos.y),
        Pos(pos.x, pos.y - 1),
        Pos(pos.x + 1, pos.y - 1),
    )


@dataclass(frozen=True)
class Unit:
    unit_id: int
    pos: Pos
    kind: str
    health: int
    level: int
    cooldown: int
    attack_range: int
    capacity: int
    backpack: tuple

    @classmethod
    def load(cls, raw):
        raw_capacity = raw.get("backPackCapability")
        return cls(
            int(raw.get("id") or 0),
            Pos.load(raw["pos"]),
            str(raw["roleType"]),
            int(raw["health"]),
            int(raw.get("level") or 0),
            int(raw.get("cooldown") or 0),
            int(raw.get("attackRange") or 0),
            int(raw_capacity) if raw_capacity is not None else 0,
            tuple(str(item) for item in raw.get("backpack") or ()),
        )

    @property
    def backpack_used(self):
        return len(self.backpack)

    @property
    def backpack_full(self):
        if self.capacity is None or self.capacity == 0:
            return False
        return self.backpack_used >= self.capacity

    @property
    def backpack_almost_full(self):
        if self.capacity is None or self.capacity == 0:
            return False
        return self.backpack_used >= self.capacity * 0.6

    def range_of_attack(self):
        if self.attack_range > 0:
            return self.attack_range
        table = TOWER_RANGE_BY_LEVEL.get(self.kind)
        if table is None:
            return 0
        level = min(max(self.level, 1), len(table))
        return table[level - 1]

    def multi_target_count(self):
        table = TOWER_MULTI_TARGET.get(self.kind)
        if table is None:
            return 1
        level = min(max(self.level, 1), len(table))
        return table[level - 1]


@dataclass(frozen=True)
class Robot:
    robot_id: int
    pos: Pos
    kind: str
    health: int
    abnormal_state: str
    target_team: str

    @classmethod
    def load(cls, raw):
        return cls(
            int(raw["id"]),
            Pos.load(raw["pos"]),
            str(raw.get("roleType", "")),
            int(raw["health"]),
            str(raw.get("abnormalState", "")),
            str(raw.get("targetTeam", "")),
        )

    @property
    def is_dizzy(self):
        return self.abnormal_state == "dizzy"

    @property
    def threat_score(self):
        return ROBOT_THREAT_PRIORITY.get(self.kind, 0)


@dataclass(frozen=True)
class PlayerTask:
    task_type: str
    task_position: Pos
    cold_down_rounds: int
    score_reward: int
    gold_reward: int
    is_valid: bool
    timeout_rounds: int

    @classmethod
    def load(cls, raw):
        return cls(
            str(raw.get("taskType", "")),
            Pos.load(raw["taskPosition"]),
            int(raw.get("coldDownRounds") or 0),
            int(raw.get("scoreReward") or 0),
            int(raw.get("goldReward") or 0),
            bool(raw.get("isValid", False)),
            int(raw.get("timeoutRounds") or 0),
        )


@dataclass(frozen=True)
class Turn:
    round_no: int
    is_day: bool
    gold: int
    width: int
    height: int
    zones: dict
    ours: tuple
    robots: tuple
    vendor_prices: dict
    weapon_shop: list
    world_news: dict
    phase_task: str
    errors: list
    last_action_results: dict
    team_type: str
    player_tasks: tuple
    llm_resp: str

    @classmethod
    def load(cls, payload):
        round_no = int(payload["roundNo"])
        info = payload["mapInfo"]
        team = payload["teamOur"]
        vendor_prices = {
            item["name"]: int(item["price"])
            for item in (payload.get("vendorShopList") or [])
        }
        weapon_shop = payload.get("weaponShopList") or []
        world_news = payload.get("worldNews") or {}
        errors = payload.get("errors") or []
        last_results_raw = payload.get("lastRoundRoleActionResults") or {}
        last_action_results = {
            int(k): bool(v) for k, v in last_results_raw.items()
        }
        player_tasks = tuple(
            PlayerTask.load(pt)
            for pt in (team.get("playerTasks") or [])
        )
        llm_resp = str(payload.get("llmResp") or "")
        return cls(
            round_no,
            (round_no - 1) % ROUNDS_PER_DAY < DAY_ROUNDS,
            int(team.get("goldNum") or 0),
            int(info["width"]),
            int(info["height"]),
            {
                Pos.load(zone["pos"]): str(zone["neutralType"])
                for zone in info.get("zones") or ()
            },
            tuple(Unit.load(role) for role in team.get("roles") or ()),
            tuple(
                Robot.load(robot)
                for robot in (payload.get("robot") or {}).get("roles") or ()
            ),
            vendor_prices,
            weapon_shop,
            world_news,
            str(payload.get("phaseTask") or ""),
            errors,
            last_action_results,
            str(team.get("type") or ""),
            player_tasks,
            llm_resp,
        )

    @property
    def day_number(self):
        return (self.round_no - 1) // ROUNDS_PER_DAY + 1

    @property
    def round_in_day(self):
        return (self.round_no - 1) % ROUNDS_PER_DAY + 1

    @property
    def rounds_remaining_today(self):
        if self.is_day:
            return DAY_ROUNDS - self.round_in_day + 1
        return ROUNDS_PER_DAY - self.round_in_day + 1

    @property
    def is_night_first_round(self):
        return not self.is_day and self.round_in_day == DAY_ROUNDS + 1

    @property
    def is_day_first_round(self):
        return self.is_day and self.round_in_day == 1

    def station(self):
        for unit in self.ours:
            if unit.kind == STATION:
                return unit
        return None

    def alive(self, kinds):
        return tuple(
            unit for unit in self.ours
            if unit.kind in kinds and unit.health > 0
        )

    def controllable(self):
        return tuple(sorted(
            self.alive(CONTROLLABLE_TYPES), key=lambda u: u.unit_id,
        ))

    def workers(self):
        return tuple(sorted(
            self.alive((WORKER,)), key=lambda u: u.unit_id,
        ))

    def weapons(self):
        return tuple(sorted(
            self.alive(TOWER_TYPES),
            key=lambda u: (u.pos.x, u.pos.y),
        ))

    def walls(self):
        return self.alive((WALL,))

    def footprint(self, unit):
        if unit.kind == STATION:
            return station_footprint(unit.pos)
        return (unit.pos,)

    def land(self, pos):
        if not 0 <= pos.x < self.width or not 0 <= pos.y < self.height:
            return False
        return self.zones.get(pos, LAND) == LAND

    def occupied_cells(self):
        cells = set()
        for unit in self.ours:
            cells.update(self.footprint(unit))
        return frozenset(cells)

    def blocked(self, moving):
        cells = {pos for pos, kind in self.zones.items() if kind != LAND}
        cells.update(self.occupied_cells())
        cells.discard(moving.pos)
        for robot in self.robots:
            cells.add(robot.pos)
        return frozenset(cells)

    def stone_mines(self):
        return tuple(pos for pos, kind in self.zones.items() if kind == "stone")

    def iron_mines(self):
        return tuple(pos for pos, kind in self.zones.items() if kind == "iron")

    def copper_mines(self):
        return tuple(pos for pos, kind in self.zones.items() if kind == "copper")

    def vendor_pos(self):
        for pos, kind in self.zones.items():
            if kind == "vendor":
                return pos
        return None

    def weapon_shop_pos(self):
        for pos, kind in self.zones.items():
            if kind == "weaponShop":
                return pos
        return None

    def valid_tasks(self):
        return tuple(
            pt for pt in self.player_tasks
            if pt.is_valid and pt.cold_down_rounds == 0
        )

    @property
    def folk_legends(self):
        return str(self.world_news.get("folkLegends", ""))

    @property
    def official_news(self):
        return str(self.world_news.get("officialNews", ""))

    def has_active_task(self):
        return bool(self.phase_task)


def move_command(pos):
    return {"action": "move", "targetPos": [pos.dump()]}


def collect_command(pos):
    return {"action": "collect", "targetPos": [pos.dump()]}


def build_command(pos, name):
    return {"action": "build", "targetPos": [pos.dump()], "name": name}


def remove_command(pos):
    return {"action": "remove", "targetPos": [pos.dump()]}


def attack_command(controller_id, targets):
    return {
        "action": "attack",
        "targetPos": [p.dump() for p in targets],
        "controllerId": str(controller_id),
    }


def sell_command(name, num=1):
    return {"action": "sell", "name": name, "num": num}


def buy_command(name, num=1):
    return {"action": "buy", "name": name, "num": num}


def use_command(name, target=None):
    cmd = {"action": "use", "name": name}
    if target is not None:
        cmd["targetPos"] = [target.dump()]
    return cmd


def drop_command(name):
    return {"action": "drop", "name": name}


def accept_task_command():
    return {"action": "acceptTask"}


def submit_answer_command(answer):
    return {"action": "submitAnswer", "taskAnswer": str(answer)}


def summon_treasure_command(target_pos, items):
    return {
        "action": "summonTreasure",
        "targetPos": [target_pos.dump()],
        "item": list(items),
    }
