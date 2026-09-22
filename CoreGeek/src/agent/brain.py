"""
决策核心 — 分阶段调度白天/夜晚行为
"""
import logging
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
    accept_task_command,
    submit_answer_command,
    summon_treasure_command,
)

LOGGER = logging.getLogger(__name__)

TOWER_LOADOUT = ("gatling", "railgun", "rocket")
STONE_BATCH = 10
_NEIGHBOUR_STEPS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
)

# ── LLM 调用计数（每游戏日限3次，任务期间不限） ──
_llm_call_count = {"day": 0, "count": 0}
MAX_LLM_CALLS_PER_DAY = 3


def _can_use_llm(turn: Turn) -> bool:
    """检查是否还能调用 LLM（非任务期间每游戏日限3次）"""
    current_day = turn.day_number
    if _llm_call_count["day"] != current_day:
        _llm_call_count["day"] = current_day
        _llm_call_count["count"] = 0
    return _llm_call_count["count"] < MAX_LLM_CALLS_PER_DAY


def _record_llm_call(turn: Turn) -> None:
    _llm_call_count["day"] = turn.day_number
    _llm_call_count["count"] += 1


# ── 官方新闻分析 ───────────────────────────────────────

def _analyze_ore_news(turn: Turn) -> dict[str, int]:
    """分析官方消息，返回矿石优先级加成 {ore_type: bonus}

    关键词匹配（中文）：
    - 提到某矿石+停工/短缺/无法采集 → 该矿石价格会上涨 → 优先采集
    - 提到某矿石+恢复/复产 → 价格回落 → 正常优先级
    """
    news = turn.official_news
    if not news or news == "今日无重大新闻":
        return {}

    bonuses: dict[str, int] = {}
    # 铁矿波动检测
    if any(kw in news for kw in ("铁矿", "铁矿区", "铁矿脉")):
        if any(kw in news for kw in ("停工", "塌方", "短缺", "无法采集", "修复")):
            bonuses["iron"] = 10  # 铁涨价，优先采
        elif any(kw in news for kw in ("恢复", "复产", "重新开放")):
            bonuses["iron"] = -5  # 铁降价，降低优先级

    # 铜矿波动
    if any(kw in news for kw in ("铜矿", "铜矿区")):
        if any(kw in news for kw in ("停工", "塌方", "短缺", "无法采集")):
            bonuses["copper"] = 10
        elif any(kw in news for kw in ("恢复", "复产")):
            bonuses["copper"] = -5

    # 石矿波动
    if any(kw in news for kw in ("石矿", "采石")):
        if any(kw in news for kw in ("停工", "塌方", "短缺")):
            bonuses["stone"] = 10
        elif any(kw in news for kw in ("恢复", "复产")):
            bonuses["stone"] = -5

    return bonuses


# ── 机器人召唤令 ────────────────────────────────────────

def _try_buy_summon_orders(turn, role, commands):
    """白天购买机器人召唤令骚扰敌方"""
    if role.backpack_full:
        return False
    # 只在夜晚来临前购买（白天回合数 > 50 时开始准备）
    if turn.round_in_day < 55:
        return False

    gold = turn.gold
    # 优先级：BOSS > 大型 > 中型 > 小型
    summon_orders = [
        ("BossRobotSummonOrder", 200),
        ("LargeRobotSummonOrder", 100),
        ("MiddleRobotSummonOrder", 30),
        ("SmallRobotSummonOrder", 20),
    ]
    for name, price in summon_orders:
        if name in role.backpack:
            continue  # 已有，不要重复买
        if gold >= price:
            commands[role.unit_id] = buy_command(name, 1)
            return True
    return False


def decide(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    commands: dict[int, dict[str, Any]] = {}
    prompt = ""
    turn = None
    try:
        turn = Turn.load(payload)
    except Exception as e:
        LOGGER.exception("Turn.load failed: %s", e)
        return {"roleCommandMap": {}, "prompt": "", "executeCmd": ""}

    # 自动重试（独立 try，失败不影响主逻辑）
    try:
        _auto_retry(turn, commands)
        # 追踪建造失败：上回合角色动作失败 → 记录失败位置
        for uid, ok in turn.last_action_results.items():
            if not ok:
                for u in turn.ours:
                    if u.unit_id == uid and u.kind == WORKER:
                        # 工人失败，可能是建造失败 → 查找该工人附近的塔位
                        for site in _tower_sites(turn):
                            if chebyshev(u.pos, site) <= 1:
                                key = (site.x, site.y)
                                _build_failures[key] = _build_failures.get(key, 0) + 1
                                LOGGER.info("build failure tracked at %s (count=%d)", site, _build_failures[key])
                                break
    except Exception as e:
        LOGGER.exception("_auto_retry failed: %s", e)

    # 主决策（独立 try）
    try:
        if turn.is_day:
            _day(turn, commands)
        else:
            _night(turn, commands)
    except Exception as e:
        LOGGER.exception("day/night decision failed: %s", e)

    # 收集 LLM prompt
    try:
        prompt = (
            getattr(_pioneer_do_task, "_pending_prompt", "")
            or getattr(_try_summon_treasure, "_pending_prompt", "")
        )
        _pioneer_do_task._pending_prompt = ""
        _try_summon_treasure._pending_prompt = ""
        retry_hint = _retry_hint(turn)
        if retry_hint and not prompt and _can_use_llm(turn):
            prompt = retry_hint
            _record_llm_call(turn)
    except Exception as e:
        LOGGER.exception("prompt collection failed: %s", e)

    # ══ 最终兜底：无论发生什么，确保每回合至少每个可控角色有一条命令 ═══
    try:
        if not commands:
            for role in turn.controllable():
                if role.unit_id not in commands:
                    commands[role.unit_id] = {
                        "action": "move",
                        "targetPos": [role.pos.dump()],
                    }
    except Exception as e:
        LOGGER.exception("fallback commands failed: %s", e)

    LOGGER.info(
        "round %s day %s -> %d commands",
        turn.round_no, turn.day_number, len(commands),
    )
    return {
        "roleCommandMap": {str(k): v for k, v in commands.items()},
        "prompt": prompt,
        "executeCmd": "",
    }


def _auto_retry(turn: Turn, commands: dict[int, dict[str, Any]]) -> None:
    """上回合失败指令的记录（实际替代方案由 _day/_night 中的 fallback 处理）"""
    # 只做日志记录，不在此发命令（避免与 _day/_night 冲突）
    for uid, ok in turn.last_action_results.items():
        if not ok:
            LOGGER.info("last round action failed for role %s", uid)


def _retry_hint(turn: Turn) -> str:
    """根据上回合失败结果生成 LLM 重试提示"""
    failed = [uid for uid, ok in turn.last_action_results.items() if not ok]
    if not failed:
        return ""
    failed_types = []
    for uid in failed:
        for unit in turn.ours:
            if unit.unit_id == uid:
                failed_types.append(f"{unit.kind}(id={uid})")
                break
    if not failed_types:
        return ""
    return (
        f"上回合以下角色指令执行失败：{', '.join(failed_types)}。"
        f"请分析可能原因并给出替代方案。"
    )


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

    # 每个工人独立 try，一个失败不影响另一个
    if day <= 2:
        # 前两天：工人1建炮台，工人2采石头备料
        if len(workers) >= 1:
            try:
                _worker_builder(turn, workers[0], free_towers, free_walls, claimed, commands)
            except Exception as e:
                LOGGER.exception("worker builder failed (id=%s): %s", workers[0].unit_id, e)
        if len(workers) >= 2:
            try:
                _mine_stone(turn, workers[1], claimed, commands)
            except Exception as e:
                LOGGER.exception("worker stone failed (id=%s): %s", workers[1].unit_id, e)
    else:
        # 工人1：采石头 + 建围墙
        if len(workers) >= 1:
            try:
                _worker_builder(turn, workers[0], free_towers, free_walls, claimed, commands)
            except Exception as e:
                LOGGER.exception("worker builder failed (id=%s): %s", workers[0].unit_id, e)
        # 工人2：采铜铁 + 售卖
        if len(workers) >= 2:
            try:
                _worker_miner_seller(turn, workers[1], free_towers, claimed, commands)
            except Exception as e:
                LOGGER.exception("worker miner_seller failed (id=%s): %s", workers[1].unit_id, e)

    pioneer = _find_pioneer(turn)
    if pioneer is not None:
        try:
            _pioneer_day(turn, pioneer, claimed, commands)
        except Exception as e:
            LOGGER.exception("pioneer day failed (id=%s): %s", pioneer.unit_id, e)

    # ─ 安全网：如果没有任何命令，给每个可控角色发原地待命 ──
    if not commands:
        for role in turn.controllable():
            if role.unit_id not in commands:
                commands[role.unit_id] = {"action": "move", "targetPos": [role.pos.dump()]}

    # ── 提前回防：夜晚来临前5回合，工人走向武器 ──
    if turn.round_in_day >= 65:
        weapons = turn.weapons()
        for role in turn.controllable():
            if role.unit_id in commands:
                continue  # 已有指令，跳过
            if not weapons:
                continue
            nearest = min(weapons, key=lambda w: chebyshev(role.pos, w.pos))
            if chebyshev(role.pos, nearest.pos) > 1:
                step = _step_toward(turn, role, nearest.pos, claimed)
                if step is not None:
                    commands[role.unit_id] = move_command(step)


# ── 建造失败追踪（避免同一位置反复失败） ──
_build_failures: dict[tuple, int] = {}
MAX_BUILD_FAILURES = 3


def _worker_build_tower(turn, role, sites, free_towers, claimed, commands):
    for idx, site in enumerate(sites):
        if site in free_towers and site not in claimed:
            key = (site.x, site.y)
            if _build_failures.get(key, 0) >= MAX_BUILD_FAILURES:
                continue  # 该位置反复失败，跳过
            tower_name = TOWER_LOADOUT[idx % len(TOWER_LOADOUT)]
            _build_or_walk(turn, role, site, tower_name, claimed, commands)
            return
    _mine_stone(turn, role, claimed, commands)


def _worker_builder(turn, role, free_towers, free_walls, claimed, commands):
    """工人1：采石头 + 建围墙 + 建炮台"""
    # 优先建炮台
    if free_towers and turn.gold >= WEAPON_BUILD_COST:
        for idx, site in enumerate(_tower_sites(turn)):
            if site in free_towers and site not in claimed:
                tower_name = TOWER_LOADOUT[idx % len(TOWER_LOADOUT)]
                _build_or_walk(turn, role, site, tower_name, claimed, commands)
                return
    # 有石头就建墙
    stones = role.backpack.count(WALL_MATERIAL)
    if stones > 0 and free_walls:
        for site in free_walls:
            if site not in claimed:
                _build_or_walk(turn, role, site, WALL, claimed, commands)
                return
    # 背包满就去卖
    if role.backpack_almost_full:
        _go_sell(turn, role, claimed, commands)
        return
    # 兜底：采石头
    _mine_stone(turn, role, claimed, commands)


def _worker_miner_seller(turn, role, free_towers, claimed, commands):
    """工人2：采铜/铁矿 + 售卖赚钱"""
    # 优先建炮台（如果有空缺）
    if free_towers and turn.gold >= WEAPON_BUILD_COST:
        for idx, site in enumerate(_tower_sites(turn)):
            if site in free_towers and site not in claimed:
                tower_name = TOWER_LOADOUT[idx % len(TOWER_LOADOUT)]
                _build_or_walk(turn, role, site, tower_name, claimed, commands)
                return
    # 背包有矿就去卖
    if role.backpack:
        _go_sell(turn, role, claimed, commands)
        return
    # 采铜（价值最高）
    copper = role.backpack.count("copper")
    if copper < 10:
        _mine_ore(turn, role, "copper", claimed, commands)
        return
    # 采铁
    iron = role.backpack.count("iron")
    if iron < 10:
        _mine_ore(turn, role, "iron", claimed, commands)
        return
    # 铜铁都够了，采石头
    _mine_stone(turn, role, claimed, commands)


def _pioneer_day(turn, role, claimed, commands):
    # 1. 如果有正在执行的任务，提交答案
    if turn.has_active_task():
        _pioneer_do_task(turn, role, claimed, commands)
        return

    # 2. 有可领取的任务，前往任务点领取
    valid_tasks = turn.valid_tasks()
    if valid_tasks:
        _pioneer_go_task(turn, role, valid_tasks, claimed, commands)
        return

    # 3. 没有任务时：购买任务用品 + 探索
    _pioneer_idle(turn, role, claimed, commands)


# ── 开拓者任务系统 ─────────────────────────────────────

def _pioneer_do_task(turn, role, claimed, commands):
    """执行已领取的自进化任务：用 LLM 获取答案并提交"""
    task = turn.phase_task
    if not task:
        return

    # 如果背包中有 Medicine 且血量低，先用药
    if "Medicine" in role.backpack and role.health < 110:
        commands[role.unit_id] = use_command("Medicine")
        return

    # 上回合 LLM 返回了答案，直接提交
    if turn.llm_resp:
        commands[role.unit_id] = submit_answer_command(turn.llm_resp)
        return

    # 任务期间 LLM 不限次数
    prompt = (
        f"你是一个自进化AI助手。当前任务：\n{task}\n\n"
        f"请给出简洁的答案，只输出答案内容，不要解释。"
    )
    _pioneer_do_task._pending_prompt = prompt
    _record_llm_call(turn)
    commands[role.unit_id] = {"action": "move", "targetPos": [role.pos.dump()]}


# 存储待发送的 prompt
_pioneer_do_task._pending_prompt = ""


def _pioneer_go_task(turn, role, valid_tasks, claimed, commands):
    """前往任务点领取任务"""
    # 选择奖励最高的有效任务
    best = max(valid_tasks, key=lambda t: (t.score_reward, t.gold_reward))
    target = best.task_position

    # 任务点2占2格，到达任一格子即可
    if chebyshev(role.pos, target) <= 1:
        commands[role.unit_id] = accept_task_command()
        return

    step = _step_toward(turn, role, target, claimed)
    if step is not None:
        commands[role.unit_id] = move_command(step)


def _pioneer_idle(turn, role, claimed, commands):
    """开拓者空闲时：购买任务用品 + 宝藏召唤 + 巡逻"""
    # 0. 优先尝试宝藏召唤
    if _try_summon_treasure(turn, role, claimed, commands):
        return

    shop = turn.weapon_shop_pos()

    # 1. 如果背包有空位且有金币，去购买任务用品
    task_items = [
        "AcientTablet", "StarSand", "FlameBreath",
        "FrostPotion", "ThornAmulet", "IronWhistle",
    ]
    needed_items = [i for i in task_items if i not in role.backpack]

    if needed_items and turn.gold >= 15 and shop:
        if chebyshev(role.pos, shop) <= 1:
            commands[role.unit_id] = buy_command(needed_items[0], 1)
            return
        else:
            step = _step_toward(turn, role, shop, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)
                return

    # 2. 没有东西买时，往任务点方向巡逻
    valid_tasks = turn.valid_tasks()
    if valid_tasks:
        best = max(valid_tasks, key=lambda t: t.score_reward)
        if chebyshev(role.pos, best.task_position) > 5:
            step = _step_toward(turn, role, best.task_position, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)
                return

    # 3. 完全空闲，往基地方向靠拢
    station = turn.station()
    if station and chebyshev(role.pos, station.pos) > 6:
        step = _step_toward(turn, role, station.pos, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)


# ── 夜晚开拓者行为 ──────────────────────────────────────

def _pioneer_night(turn, role, claimed, commands):
    """夜晚开拓者应回到基地附近安全位置"""
    station = turn.station()
    if station is None:
        return
    # 优先回到有武器的位置
    weapons = turn.weapons()
    if weapons:
        nearest_weapon = min(weapons, key=lambda w: chebyshev(role.pos, w.pos))
        if chebyshev(role.pos, nearest_weapon.pos) > 1:
            step = _step_toward(turn, role, nearest_weapon.pos, claimed)
            if step is not None:
                commands[role.unit_id] = move_command(step)


# ── 宝藏召唤系统 ────────────────────────────────────────

# 任务用品全集
_ALL_TASK_ITEMS = (
    "AcientTablet", "StarSand", "FlameBreath",
    "FrostPotion", "ThornAmulet", "IronWhistle",
)


def _try_summon_treasure(turn, role, claimed, commands):
    """尝试召唤宝藏：解析传闻 → 定位祭坛 → 带齐物品 → 召唤"""
    legends = turn.folk_legends
    if not legends:
        return False

    # 上回合 LLM 返回了解析结果
    if turn.llm_resp:
        try:
            return _process_treasure_llm_result(turn, role, claimed, commands)
        except Exception as e:
            LOGGER.exception("treasure llm result parse failed: %s", e)
            return False

    # 检查 LLM 配额（非任务期间每游戏日限3次）
    if not _can_use_llm(turn):
        return False

    prompt = (
        f"分析以下民间传闻，提取宝藏信息：\n{legends}\n\n"
        f"输出JSON格式：\n"
        f'{{"altar_pos": {{"x": int, "y": int}}, '
        f'"required_items": ["item1", "item2", ...], '
        f'"open_day": int 或 null}}\n'
        f"如果信息不足以确定，required_items 列出所有6种任务用品。"
    )
    _try_summon_treasure._pending_prompt = prompt
    _record_llm_call(turn)
    return False


_try_summon_treasure._pending_prompt = ""


def _process_treasure_llm_result(turn, role, claimed, commands):
    """处理 LLM 返回的宝藏解析结果"""
    import json
    raw = turn.llm_resp.strip()

    # 尝试提取 JSON
    try:
        # 找到第一个 { 到最后一个 }
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return False

    altar_pos_raw = data.get("altar_pos")
    required_items = data.get("required_items", list(_ALL_TASK_ITEMS))

    if not altar_pos_raw:
        return False

    altar = Pos(int(altar_pos_raw["x"]), int(altar_pos_raw["y"]))

    # 检查背包中有哪些所需物品
    have_items = [i for i in required_items if i in role.backpack]
    need_items = [i for i in required_items if i not in role.backpack]

    # 如果还缺物品，去购买
    if need_items:
        shop = turn.weapon_shop_pos()
        if shop and turn.gold >= 15:
            if chebyshev(role.pos, shop) <= 1:
                commands[role.unit_id] = buy_command(need_items[0], 1)
                return True
            else:
                step = _step_toward(turn, role, shop, claimed)
                if step is not None:
                    commands[role.unit_id] = move_command(step)
                    return True
        return False

    # 物品齐全，前往祭坛
    if chebyshev(role.pos, altar) <= 1:
        commands[role.unit_id] = summon_treasure_command(altar, have_items)
        return True
    else:
        step = _step_toward(turn, role, altar, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)
            return True
        return False


def _night(turn, commands):
    claimed: set[Pos] = set()
    enemy_robots = tuple(
        r for r in turn.robots
        if r.target_team == turn.team_type and not r.is_dizzy
    )
    weapons = turn.weapons()
    controllable = turn.controllable()

    # ── 角色物品使用（夜晚优先保命） ──
    for role in controllable:
        _role_use_items_night(turn, role, enemy_robots, commands)

    # ── 优先工人配对武器 ─
    workers = turn.workers()
    pioneer = _find_pioneer(turn)
    assigned_roles: set[int] = set()
    assigned_weapons: set[int] = set()

    for worker in workers:
        if worker.unit_id in assigned_roles:
            continue
        weapon = _best_unassigned_weapon(worker, weapons, assigned_weapons)
        if weapon is None:
            continue
        _operate_or_approach(turn, worker, weapon, enemy_robots, claimed, commands)
        assigned_roles.add(worker.unit_id)
        assigned_weapons.add(weapon.unit_id)

    # ── 开拓者配对剩余武器 ──
    if pioneer is not None and pioneer.unit_id not in assigned_roles:
        weapon = _best_unassigned_weapon(pioneer, weapons, assigned_weapons)
        if weapon is not None:
            _operate_or_approach(turn, pioneer, weapon, enemy_robots, claimed, commands)
            assigned_roles.add(pioneer.unit_id)
            assigned_weapons.add(weapon.unit_id)
        else:
            _pioneer_night(turn, pioneer, claimed, commands)

    # ── 未配对角色前往最近武器 ──
    for role in controllable:
        if role.unit_id in assigned_roles:
            continue
        if not weapons:
            continue
        nearest = min(weapons, key=lambda t: chebyshev(role.pos, t.pos))
        if chebyshev(role.pos, nearest.pos) <= 1:
            continue
        step = _step_toward(turn, role, nearest.pos, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)

    #  安全网：无命令时发原地待命 ──
    if not commands:
        for role in controllable:
            commands[role.unit_id] = {"action": "move", "targetPos": [role.pos.dump()]}


def _best_unassigned_weapon(role, weapons, assigned_weapons):
    """为角色选择最近的未分配武器（跳过冷却中的武器）"""
    candidates = [
        w for w in weapons
        if w.unit_id not in assigned_weapons and w.cooldown <= 0
    ]
    if not candidates:
        # 所有武器都在冷却 → 选最近的（走过去等冷却结束）
        candidates = [w for w in weapons if w.unit_id not in assigned_weapons]
    if not candidates:
        return None
    return min(candidates, key=lambda w: chebyshev(role.pos, w.pos))


def _operate_or_approach(turn, role, weapon, enemy_robots, claimed, commands):
    """角色操作武器或走向武器"""
    dist = chebyshev(role.pos, weapon.pos)
    if dist <= 1:
        if weapon.cooldown > 0:
            return
        targets = _select_attack_targets(weapon, enemy_robots, turn)
        if targets:
            commands[weapon.unit_id] = attack_command(role.unit_id, targets)
    else:
        step = _step_toward(turn, role, weapon.pos, claimed)
        if step is not None:
            commands[role.unit_id] = move_command(step)


def _role_use_items_night(turn, role, enemy_robots, commands):
    """夜晚角色自动使用物品"""
    if role.unit_id in commands:
        return

    bp = role.backpack

    # 使用机器人召唤令（夜晚第一回合使用，叠加到敌方夜晚）
    summon_orders = [
        "BossRobotSummonOrder", "LargeRobotSummonOrder",
        "MiddleRobotSummonOrder", "SmallRobotSummonOrder",
    ]
    if turn.is_night_first_round:
        for order in summon_orders:
            if order in bp:
                commands[role.unit_id] = use_command(order)
                return

    # 血量低用药
    if "Medicine" in bp and role.health < 110:
        commands[role.unit_id] = use_command("Medicine")
        return

    # 机器人密集时用范围炸弹
    if "Bomb" in bp and enemy_robots:
        cluster = _find_robot_cluster(enemy_robots)
        if cluster is not None:
            commands[role.unit_id] = use_command("Bomb", cluster)
            return

    # 机器人密集时用眩晕法宝
    if "DizzyWeapon" in bp and enemy_robots:
        cluster = _find_robot_cluster(enemy_robots)
        if cluster is not None:
            commands[role.unit_id] = use_command("DizzyWeapon", cluster)
            return


def _find_robot_cluster(robots):
    """找到机器人最密集的中心位置"""
    if not robots:
        return None
    # 简单方法：取所有机器人坐标的平均值
    cx = sum(r.pos.x for r in robots) // len(robots)
    cy = sum(r.pos.y for r in robots) // len(robots)
    return Pos(cx, cy)


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
        energy = tower.attack_power
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
        # 官方新闻加成：涨价矿石优先卖
        bonuses = _analyze_ore_news(turn)
        items = sorted(
            set(role.backpack),
            key=lambda n: prices.get(n, 0) + bonuses.get(n, 0),
            reverse=True,
        )
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


def _try_use_upgrade_vouchers(turn, role, commands):
    """白天自动使用背包中的升级券/消耗品（需要角色在目标建筑 1 格内）"""
    bp = role.backpack

    # 使用 Medicine（无需指定位置）
    if "Medicine" in bp and role.health < 200:
        commands[role.unit_id] = use_command("Medicine")
        return True

    # 使用武器升级券 — 需要角色在武器 1 格内
    for voucher, level_check in [
        ("WeaponUpgradeVoucher1", 1),
        ("WeaponUpgradeVoucher2", 2),
    ]:
        if voucher not in bp:
            continue
        for w in turn.weapons():
            if w.level == level_check and chebyshev(role.pos, w.pos) <= 1:
                commands[role.unit_id] = use_command(voucher, w.pos)
                return True

    # 使用基地升级券
    station = turn.station()
    if station:
        for voucher, level_check in [
            ("StationUpgradeVoucher1", 1),
            ("StationUpgradeVoucher2", 2),
        ]:
            if voucher in bp and station.level == level_check and chebyshev(role.pos, station.pos) <= 1:
                commands[role.unit_id] = use_command(voucher, station.pos)
                return True

    # 使用围墙升级券
    for voucher, level_check in [
        ("WallUpgradeVoucher1", 1),
        ("WallUpgradeVoucher2", 2),
    ]:
        if voucher not in bp:
            continue
        for w in turn.walls():
            if w.level == level_check and chebyshev(role.pos, w.pos) <= 1:
                commands[role.unit_id] = use_command(voucher, w.pos)
                return True

    # 使用围墙修复包
    if "WallFixer" in bp:
        low_walls = [w for w in turn.walls() if w.health < 800]
        for w in low_walls:
            if chebyshev(role.pos, w.pos) <= 1:
                commands[role.unit_id] = use_command("WallFixer", w.pos)
                return True

    return False


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
