#!/usr/bin/env python3
"""本地测试：用 request.txt 跑 decide() 定位崩溃点"""
import json, sys, os

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(root, "src"))

from agent.brain import decide
from agent.protocol import Turn

def load_request(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def test_round_1():
    """模拟 round 1：lastRoundRoleActionResults 为空"""
    payload = load_request(os.path.join(root, "..", "Competition-main", "docs", "request.txt"))
    payload["roundNo"] = 1
    payload["lastRoundRoleActionResults"] = {}
    payload["llmResp"] = ""
    payload["phaseTask"] = ""

    print("=== Round 1 ===")
    try:
        result = decide(payload)
        cmds = result.get("roleCommandMap", {})
        print(f"commands: {len(cmds)}")
        for k, v in cmds.items():
            print(f"  role {k}: {json.dumps(v, ensure_ascii=False)}")
    except Exception as e:
        print(f"CRASH: {e}")
        import traceback; traceback.print_exc()

def test_round_2():
    """模拟 round 2：lastRoundRoleActionResults 有数据"""
    payload = load_request(os.path.join(root, "..", "Competition-main", "docs", "request.txt"))
    payload["roundNo"] = 2
    payload["lastRoundRoleActionResults"] = {
        "10010": True,
        "10011": True,
        "10012": True,
        "10013": True,
        "10020": True,
        "10030": False,
        "10040": True,
    }
    payload["llmResp"] = ""
    payload["phaseTask"] = ""

    print("\n=== Round 2 ===")
    try:
        result = decide(payload)
        cmds = result.get("roleCommandMap", {})
        print(f"commands: {len(cmds)}")
        for k, v in cmds.items():
            print(f"  role {k}: {json.dumps(v, ensure_ascii=False)}")
    except Exception as e:
        print(f"CRASH: {e}")
        import traceback; traceback.print_exc()

def test_round_2_empty_results():
    """模拟 round 2：lastRoundRoleActionResults 全部 true"""
    payload = load_request(os.path.join(root, "..", "Competition-main", "docs", "request.txt"))
    payload["roundNo"] = 2
    payload["lastRoundRoleActionResults"] = {
        "10010": True, "10011": True, "10012": True,
        "10013": True, "10020": True, "10030": True, "10040": True,
    }
    payload["llmResp"] = ""
    payload["phaseTask"] = ""
    payload["worldNews"] = {"officialNews": "今日无重大新闻", "folkLegends": ""}

    print("\n=== Round 2 (all success, no folk legends) ===")
    try:
        result = decide(payload)
        cmds = result.get("roleCommandMap", {})
        print(f"commands: {len(cmds)}")
        for k, v in cmds.items():
            print(f"  role {k}: {json.dumps(v, ensure_ascii=False)}")
    except Exception as e:
        print(f"CRASH: {e}")
        import traceback; traceback.print_exc()

def test_round_2_with_llm_resp():
    """模拟 round 2：llmResp 有非 JSON 内容"""
    payload = load_request(os.path.join(root, "..", "Competition-main", "docs", "request.txt"))
    payload["roundNo"] = 2
    payload["lastRoundRoleActionResults"] = {
        "10010": True, "10011": True, "10012": True,
        "10013": True, "10020": True, "10030": True, "10040": True,
    }
    payload["llmResp"] = "This is not JSON at all, just plain text from LLM"
    payload["phaseTask"] = ""
    payload["worldNews"] = {
        "officialNews": "今日无重大新闻",
        "folkLegends": "村里最年长的采药人昨天过世了..."
    }

    print("\n=== Round 2 (with non-JSON llmResp + folkLegends) ===")
    try:
        result = decide(payload)
        cmds = result.get("roleCommandMap", {})
        print(f"commands: {len(cmds)}")
        for k, v in cmds.items():
            print(f"  role {k}: {json.dumps(v, ensure_ascii=False)}")
    except Exception as e:
        print(f"CRASH: {e}")
        import traceback; traceback.print_exc()

if __name__ == "__main__":
    test_round_1()
    test_round_2()
    test_round_2_empty_results()
    test_round_2_with_llm_resp()
