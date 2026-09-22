#!/usr/bin/env python3
"""多轮模拟测试：从 round 1 连续跑到 round 130，模拟真实对局"""
import json, sys, os

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root, "src"))

from agent.brain import decide

def load_request():
    path = os.path.join(root, "..", "Competition-main", "docs", "request.txt")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_rounds(max_round=130):
    base = load_request()
    for r in range(1, max_round + 1):
        payload = json.loads(json.dumps(base))  # deep copy
        payload["roundNo"] = r

        # 模拟 lastRoundRoleActionResults
        if r == 1:
            payload["lastRoundRoleActionResults"] = {}
        else:
            payload["lastRoundRoleActionResults"] = {
                "10010": True, "10011": True, "10012": True,
                "10013": True, "10020": True, "10030": True, "10040": True,
            }

        payload["llmResp"] = ""
        payload["phaseTask"] = ""
        payload["lastSummonTreasureResult"] = 0

        is_day = (r - 1) % 130 < 70
        day = (r - 1) // 130 + 1

        try:
            result = decide(payload)
            cmds = result.get("roleCommandMap", {})
            status = f"{len(cmds)} cmds"
            if len(cmds) == 0:
                status = "*** 0 COMMANDS ***"
            print(f"round {r:3d} day {day} {'day' if is_day else 'night':5s} -> {status}")
            if len(cmds) > 0 and r <= 5:
                for k, v in cmds.items():
                    print(f"    role {k}: {v.get('action', '?')}")
        except Exception as e:
            print(f"round {r:3d} -> *** EXCEPTION: {e} ***")
            import traceback; traceback.print_exc()
            break

if __name__ == "__main__":
    run_rounds(130)
