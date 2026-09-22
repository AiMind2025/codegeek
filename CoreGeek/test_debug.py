#!/usr/bin/env python3
"""精确定位 _day() 崩溃点"""
import json, sys, os, traceback

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root, "src"))

from agent.protocol import Turn, Pos, chebyshev
from agent.brain import (
    _day, _night, _tower_sites, _wall_order, _find_pioneer,
    _pioneer_day, _worker_miner, _worker_flex, _worker_build_tower,
    _try_summon_treasure, _pioneer_idle,
)

def load_payload(round_no=2):
    path = os.path.join(root, "..", "Competition-main", "docs", "request.txt")
    with open(path, "r", encoding="utf-8") as f:
        base = json.load(f)
    base["roundNo"] = round_no
    base["lastRoundRoleActionResults"] = {
        "10010": True, "10011": True, "10012": True,
        "10013": True, "10020": True, "10030": True, "10040": True,
    }
    base["llmResp"] = ""
    base["phaseTask"] = ""
    return base

def step_by_step(round_no=2):
    payload = load_payload(round_no)
    turn = Turn.load(payload)
    print(f"Turn loaded: round={turn.round_no}, day={turn.day_number}, is_day={turn.is_day}")
    print(f"  gold={turn.gold}, team_type={turn.team_type}")
    print(f"  ours: {[(u.unit_id, u.kind, u.health) for u in turn.ours]}")
    print(f"  workers: {[w.unit_id for w in turn.workers()]}")
    print(f"  weapons: {[(w.unit_id, w.kind) for w in turn.weapons()]}")
    print(f"  pioneer: {_find_pioneer(turn)}")
    print(f"  station: {turn.station()}")
    print(f"  valid_tasks: {turn.valid_tasks()}")
    print(f"  has_active_task: {turn.has_active_task()}")
    print(f"  folk_legends: {turn.folk_legends[:50] if turn.folk_legends else '(empty)'}")
    print()

    commands = {}

    # Step 1: _tower_sites
    print("Step 1: _tower_sites()...")
    try:
        sites = _tower_sites(turn)
        print(f"  -> {sites}")
    except Exception as e:
        print(f"  -> CRASH: {e}")
        traceback.print_exc()
        return

    # Step 2: _wall_order
    print("Step 2: _wall_order()...")
    try:
        walls = _wall_order(turn)
        print(f"  -> {len(walls)} wall positions")
    except Exception as e:
        print(f"  -> CRASH: {e}")
        traceback.print_exc()
        return

    # Step 3: _day() full
    print("Step 3: _day() full...")
    try:
        _day(turn, commands)
        print(f"  -> {len(commands)} commands")
        for k, v in commands.items():
            print(f"    role {k}: {v}")
    except Exception as e:
        print(f"  -> CRASH: {e}")
        traceback.print_exc()

    # Step 4: _pioneer_day directly
    print("\nStep 4: _pioneer_day() directly...")
    pioneer = _find_pioneer(turn)
    if pioneer:
        try:
            cmds2 = {}
            _pioneer_day(turn, pioneer, set(), cmds2)
            print(f"  -> {len(cmds2)} commands: {cmds2}")
        except Exception as e:
            print(f"  -> CRASH: {e}")
            traceback.print_exc()

    # Step 5: _try_summon_treasure
    print("\nStep 5: _try_summon_treasure()...")
    if pioneer:
        try:
            cmds3 = {}
            result = _try_summon_treasure(turn, pioneer, set(), cmds3)
            print(f"  -> result={result}, commands={cmds3}")
        except Exception as e:
            print(f"  -> CRASH: {e}")
            traceback.print_exc()

    # Step 6: _pioneer_idle
    print("\nStep 6: _pioneer_idle()...")
    if pioneer:
        try:
            cmds4 = {}
            _pioneer_idle(turn, pioneer, set(), cmds4)
            print(f"  -> {len(cmds4)} commands: {cmds4}")
        except Exception as e:
            print(f"  -> CRASH: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    step_by_step(2)
