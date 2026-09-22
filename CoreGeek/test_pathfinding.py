#!/usr/bin/env python3
"""测试 A* 寻路是否正常工作"""
import json, sys, os

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root, "src"))

from agent.protocol import Turn, Pos, chebyshev
from agent.grid import next_step
from agent.brain import _step_toward, _find_pioneer

def load_payload():
    path = os.path.join(root, "..", "Competition-main", "docs", "request.txt")
    with open(path, "r", encoding="utf-8") as f:
        base = json.load(f)
    base["roundNo"] = 2
    base["lastRoundRoleActionResults"] = {}
    base["llmResp"] = ""
    base["phaseTask"] = ""
    return base

payload = load_payload()
turn = Turn.load(payload)
pioneer = _find_pioneer(turn)

print(f"Pioneer at: {pioneer.pos}")
print(f"Map: {turn.width}x{turn.height}")
print()

# Test 1: Direct A* to task point (17, 17)
target = Pos(17, 17)
print(f"Test 1: A* from {pioneer.pos} to {target}")
result = next_step(turn, pioneer, target)
print(f"  next_step -> {result}")

# Test 2: _step_toward
print(f"\nTest 2: _step_toward from {pioneer.pos} to {target}")
result2 = _step_toward(turn, pioneer, target, set())
print(f"  _step_toward -> {result2}")

# Test 3: Check blocked cells
blocked = turn.blocked(pioneer)
print(f"\nTest 3: blocked cells count = {len(blocked)}")
print(f"  Sample blocked: {list(blocked)[:10]}")

# Test 4: Check if pioneer's neighbors are passable
print(f"\nTest 4: Pioneer neighbors:")
for dx in [-1, 0, 1]:
    for dy in [-1, 0, 1]:
        if dx == 0 and dy == 0:
            continue
        np = Pos(pioneer.pos.x + dx, pioneer.pos.y + dy)
        is_land = turn.land(np)
        is_blocked = np in blocked
        print(f"  {np}: land={is_land}, blocked={is_blocked}")

# Test 5: _step_toward to weapon shop (25, 20)
shop = turn.weapon_shop_pos()
print(f"\nTest 5: _step_toward to weapon_shop {shop}")
result5 = _step_toward(turn, pioneer, shop, set())
print(f"  -> {result5}")

# Test 6: _step_toward to station (10, 24)
station = turn.station()
print(f"\nTest 6: _step_toward to station {station.pos}")
result6 = _step_toward(turn, pioneer, station.pos, set())
print(f"  -> {result6}")

# Test 7: Simple move to adjacent cell
adj = Pos(pioneer.pos.x + 1, pioneer.pos.y)
print(f"\nTest 7: _step_toward to adjacent {adj}")
result7 = _step_toward(turn, pioneer, adj, set())
print(f"  -> {result7}")
