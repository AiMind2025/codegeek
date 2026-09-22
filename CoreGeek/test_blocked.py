#!/usr/bin/env python3
"""测试：目标位置是否是 blocked"""
import json, sys, os

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root, "src"))

from agent.protocol import Turn, Pos

path = os.path.join(root, "..", "Competition-main", "docs", "request.txt")
with open(path, "r", encoding="utf-8") as f:
    base = json.load(f)
base["roundNo"] = 2
base["lastRoundRoleActionResults"] = {}

turn = Turn.load(base)
pioneer_pos = Pos(10, 12)

# 检查各个目标位置
targets = [
    ("task_point_1", Pos(14, 14)),
    ("task_point_2a", Pos(17, 17)),
    ("task_point_2b", Pos(16, 17)),
    ("weapon_shop", Pos(25, 20)),
    ("vendor", Pos(20, 16)),
    ("stone_mine", Pos(4, 24)),
    ("station", Pos(10, 24)),
    ("empty_land", Pos(15, 15)),
]

blocked = turn.blocked(type("R", (), {"pos": pioneer_pos, "unit_id": 10011})())

for name, pos in targets:
    is_land = turn.land(pos)
    is_blocked = pos in blocked
    zone = turn.zones.get(pos, "land")
    print(f"{name:15s} {pos}: land={is_land}, blocked={is_blocked}, zone={zone}")

# 关键测试：检查 (17,17) 周围的格子
print("\nNeighbors of (17,17):")
for dx in [-1, 0, 1]:
    for dy in [-1, 0, 1]:
        if dx == 0 and dy == 0:
            continue
        np = Pos(17 + dx, 17 + dy)
        is_land = turn.land(np)
        print(f"  {np}: land={is_land}, zone={turn.zones.get(np, 'land')}")
