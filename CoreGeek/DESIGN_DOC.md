# 《未来战争》参赛 AI — 开发设计文档

**版本：v4.0** | **日期：2026-09-23** | **代码：CoreGeek/src/agent/**

---

## 一、项目概览

判题器每回合 HTTP POST 发送局面 JSON → 程序返回 `roleCommandMap`（指令）、`prompt`（LLM）、`executeCmd`（沙盒）。

### 1.1 积分来源

| 来源 | 说明 |
|------|------|
| 任务积分 | 开拓者完成自进化任务，越快越高 |
| 击杀积分 | 夜晚武器击杀进攻机器人 |
| 生存积分 | 基地存活天数 × 10 × day |

### 1.2 核心节奏

- **每天 130 回合** = 70 白天（采集/建造/卖矿/任务）+ 60 夜晚（机器人进攻，武器防守）
- **共 10 天 1300 回合**
- 地图 41×32，基地固定左上或右下

---

## 二、代码架构

```
CoreGeek/
├── main3.py              # 入口：python main3.py <port>
├── pyproject.toml
└── src/agent/
    ├── __init__.py        # 公共接口导出
    ├── server.py          # HTTP 服务（ThreadingHTTPServer）
    ├── protocol.py        # 数据层：常量 / dataclass / 指令构造
    ├── grid.py            # A* 寻路：next_step() / path_length()
    ├── lore.py            # 民间传闻积累
    └── brain.py           # 决策核心：decide() → roleCommandMap
```

### 数据流

```
判题器 POST JSON
  → server.py Handler.do_POST()
    → brain.decide(payload)
      → Turn.load(payload)    # 结构化解析
      → _auto_retry()         # 记录失败
      → is_day ? _day() : _night()
      → 收集 LLM prompt / executeCmd
      → 兜底：每角色确保有命令
    → 返回 {roleCommandMap, prompt, executeCmd}
```

---

## 三、数据层（protocol.py）

### 3.1 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| DAY_ROUNDS / NIGHT_ROUNDS | 70 / 60 | 昼夜回合数 |
| ROUNDS_PER_DAY | 130 | 每天总回合 |
| MAX_ROUNDS | 1300 | 比赛总回合 |
| WEAPON_BUILD_COST | 25 | 建武器金币 |
| WALL_MATERIAL | "stone" | 围墙材料 |
| ROCKET_COOLDOWN | 3 | 火箭冷却 |
| MAX_LLM_CALLS_PER_DAY | 3 | 非任务期间每日 LLM 上限 |
| MAX_BUILD_FAILURES | 3 | 同一塔位失败上限 |
| DEFEND_ARRIVE_ROUND | 68 | 回防到达回合 |
| DEFEND_BUFFER_ROUNDS | 2 | 回防缓冲 |

### 3.2 数据结构

| 类 | 字段 | 说明 |
|----|------|------|
| **Pos** | x, y | 坐标（不可变） |
| **Unit** | unit_id, pos, kind, health, level, cooldown, attack_range, **attack_power**, capacity, backpack | 我方单位 |
| **Robot** | robot_id, pos, kind, health, abnormal_state, target_team | 场上机器人 |
| **PlayerTask** | task_type, task_position, cold_down_rounds, score_reward, gold_reward, is_valid, timeout_rounds | 任务点 |
| **Turn** | round_no, is_day, gold, width, height, zones, ours, robots, vendor_prices, weapon_shop, world_news, phase_task, errors, last_action_results, team_type, player_tasks, llm_resp, **last_cmd_result** | 局面快照 |

### 3.3 派生属性

| 属性/方法 | 说明 |
|-----------|------|
| `day_number` | 当前第几天 |
| `round_in_day` | 当天第几回合 |
| `base_corner` | "top_left" 或 "bottom_right" |
| `map_edge_side` | 地图边缘方向（不建墙那侧） |
| `wall_build_sides` | 需建墙的3个方向 |
| `weapon_direction` | 武器朝向 |
| `workers()` / `weapons()` / `walls()` | 按 id/坐标排序的存活单位 |
| `stone_mines()` / `iron_mines()` / `copper_mines()` | 各矿点坐标 |
| `blocked(moving)` | 障碍格（zone非land + 建筑 + 角色 + 机器人，排除自身） |
| `valid_tasks()` | 可接取的任务（有效且无冷却） |
| `folk_legends` / `official_news` | 世界消息 |

### 3.4 指令构造

| 函数 | 限制 |
|------|------|
| `move_command(pos)` | 目标1格内 |
| `collect_command(pos)` | 仅工人，矿点1格内 |
| `build_command(pos, name)` | 仅工人，仅白天 |
| `attack_command(controller_id, targets)` | 仅夜晚，武器射程内 |
| `sell_command(name, num)` | 小贩1格内 |
| `buy_command(name, num)` | 武器商店1格内 |
| `use_command(name, target)` | 部分需指定位置 |
| `accept_task_command()` | 仅开拓者 |
| `submit_answer_command(answer)` | 仅开拓者 |
| `summon_treasure_command(pos, items)` | 仅开拓者，祭坛1格内 |

---

## 四、寻路模块（grid.py）

A* 算法，切比雪夫距离启发，8方向移动。

| 函数 | 返回 | 说明 |
|------|------|------|
| `next_step(turn, moving, goal)` | Pos / None | 到目标的第一步 |
| `path_length(turn, moving, goal)` | int / None | 到目标的实际步数 |

**关键：** goal 是 zone（矿点/任务点/商店）时也可到达。

---

## 五、决策核心（brain.py）

### 5.1 主流程 decide()

```
Turn.load(payload)
  → _auto_retry()          # 记录失败 + 追踪建造失败位置
  → is_day ? _day() : _night()
  → 收集 LLM prompt / executeCmd
  → 兜底：每角色确保有命令
  → 返回
```

每个模块独立 try/except，一个崩溃不影响其他。

### 5.2 白天策略 _day()

#### 角色分工

| 角色 | 职责 | 函数 |
|------|------|------|
| 工人1 | 挖石头 → 建围墙 → 建炮台 | `_worker_builder()` |
| 工人2 | 建炮台 → 挖铜铁 → 卖矿 → 买升级券 | `_worker_miner_seller()` |
| 开拓者 | 执行任务 → 领任务 → 宝藏/巡逻 | `_pioneer_day()` |

#### 工人1 _worker_builder()

优先级：
1. **回防检查** — `_is_defend_time()` 到了就走
2. 有炮台空位 + 金币≥25 → 建炮台
3. **持续挖石头** — `_mine_while_adjacent("stone", 2)`
4. 背包有石头 + 有空墙位 → 建围墙
5. 背包快满 → 卖石头
6. 兜底 → 挖石头

#### 工人2 _worker_miner_seller()

优先级：
1. **回防检查**
2. 开局建炮台（有空位 + 金币≥25）
3. **武器升级流程** `_upgrade_weapon_flow()`
4. **持续挖铜/铁** — `_mine_while_adjacent(("copper","iron"), 2)`
5. 背包有矿 → 卖矿
6. 采铜(≤10) → 采铁(≤10) → 采石头

#### 持续采集 _mine_while_adjacent()

```
周围有目标矿种 + 背包未满 + 未到回防时间
  → collect（原地不动，下回合继续）
否则 → return False 交后续逻辑
```

#### 卖矿 _go_sell()

- 只卖矿石（stone/iron/copper）
- 按"收购价 + 新闻加成"降序卖
- 日志：`SELL copper x10 → +50 gold`

#### 武器升级 _upgrade_weapon_flow()

```
1. 找最低级武器
2. 全部顶级(level≥3) → 跳过
3. 背包有券 → 走到武器旁 use
4. 没券 + 金币够 → 去商店 buy
5. 钱不够 → 返回继续挖矿
```

### 5.3 基地防御布局

#### 方位判断 _base_corner()

```python
to_tl = chebyshev(station.pos, Pos(0, height-1))   # 到左上角
to_br = chebyshev(station.pos, Pos(width-1, 0))    # 到右下角
→ 左上更近 → "top_left"，否则 "bottom_right"
```

#### 武器摆放 _tower_sites()

- 候选 = 基地周围距离1~3的 land 格子
- 排序 = 敌人方向权重降序（左上基地→武器偏右下）
- 贪心选 **间隔≥2** 的3个位置（铺不满放宽到≥1）

#### 围墙 _wall_order()

| 基地位置 | 建墙方位 | 省略 |
|---------|---------|------|
| 左上 | 上 + 右 + 下 | 左（靠地图边缘） |
| 右下 | 上 + 左 + 下 | 右（靠地图边缘） |

入口朝敌人方向，Ring1+Ring2对齐形成走廊。

### 5.4 动态回防

```python
deadline = DEFEND_ARRIVE_ROUND - path_length(到武器) - DEFEND_BUFFER_ROUNDS
# = 68 - 实际步数 - 2
```

| 距武器步数 | 出发回合 |
|-----------|---------|
| 3步 | round 63 |
| 5步 | round 61 |
| 10步 | round 56 |

每回合 `_day()` 末尾检查所有角色，到 deadline 就走。

### 5.5 夜晚策略 _night()

**原则：一人一武器固定分配，不走路。**

```
1. 所有角色先使用物品（_role_use_items_night）
2. 固定分配：role_list[i] → weapons[i]
   - 不在武器旁 → 走向武器
   - 武器冷却 → 跳过
   - 有目标 → attack
3. 安全网：每角色确保有命令
```

#### 夜晚物品使用优先级

| 优先级 | 物品 | 触发条件 |
|--------|------|---------|
| 1 | 召唤令 | 夜晚首回合 |
| 2 | Medicine | 血量 < 110 |
| 3 | Bomb | 有机器人密集簇 |
| 4 | DizzyWeapon | 有机器人密集簇 |

#### 攻击目标选择

通用排序：威胁度降序 → 距基地升序 → 血量升序

| 武器 | 策略 |
|------|------|
| 加特林 | 90°锥形约束，选同方向最多 n 个 |
| 电磁炮 | 遍历候选，算弹道穿透总伤害，选最大 |
| 火箭 | 3×3区域去重，选不同区域 n 个 |

---

## 六、开拓者任务系统

### 6.1 白天三级优先级

```
1. 有已领任务（phase_task 非空）→ _pioneer_do_task()
2. 有可接任务 → _pioneer_go_task()
3. 空闲 → _pioneer_idle()
```

### 6.2 自进化任务 _pioneer_do_task() — executeCmd 沙盒闭环

```
接任务 → LLM分析任务 → 发executeCmd → 读lastCmdResult
    ↑                                      ↓
    └── LLM根据输出决定下一步 ←────────────
                              ↓
                        得出答案 → submitAnswer
```

- 结果未变 → 等待（发 move 原地）
- 连续8次相同结果 → 提交 N/A 防卡死

### 6.3 领取任务 _pioneer_go_task()

选奖励最高的有效任务 → 走到1格内 → acceptTask

### 6.4 空闲 _pioneer_idle()

```
1. 宝藏召唤（day≥5）
2. 买任务用品
3. 冷却期间也走向任务点（提前就位）
4. 到了但还在冷却 → 帮忙采石头
```

### 6.5 宝藏召唤 _try_summon_treasure() — 状态机

```
idle → buying → moving → summoning → done
```

| 阶段 | 行为 |
|------|------|
| idle | 等 LLM 解析传闻 |
| buying | 买缺失物品 |
| moving | 前往祭坛 / 等开启日 |
| summoning | 到达祭坛召唤 |

**天数阈值：** attackmap 第8天、attackmap_update 第5天前跳过。

---

## 七、推理类任务（官方消息）

`_analyze_ore_news()` 关键词分析：

| 关键词 | 效果 |
|--------|------|
| 铁矿+停工/塌方 | 铁涨价 → 优先采铁，bonus=+10 |
| 铜矿+短缺 | 铜涨价 → 优先采铜 |
| 恢复/复产 | 价格回落 → bonus=-5 |

影响 `_go_sell()` 的卖出排序和 `_worker_miner_seller()` 的采集优先级。

---

## 八、长上下文任务（民间传闻）

`lore.py` 积累模块：
- 每天 `append_folklore()` 追加传闻（去重）
- `get_all_folklore()` 获取全部累积
- 宝藏召唤时综合分析多天线索

---

## 九、LLM 调用管理

| 函数 | 说明 |
|------|------|
| `_can_use_llm(turn)` | 非任务期间每游戏日限3次（按天重置） |
| `_record_llm_call(turn)` | 记录调用 |
| 任务期间 | **不限次数** |

---

## 十、建造失败追踪

`_build_failures: dict[(x,y), int]`
- 上回合工人指令失败 → 附近塔位计数+1
- 寻路失败也记录
- 同位置≥3次 → 永久跳过

---

## 十一、安全网

### 白天
```python
for role in turn.controllable():
    if role.unit_id not in commands:
        commands[role.unit_id] = {"action": "move", "targetPos": [role.pos.dump()]}
```

### 夜晚
同上，确保每人有命令。

### 基地应急
stHP < 500 → 所有工人停止采矿，全力建墙。

---

## 十二、日志规范

| 前缀 | 场景 |
|------|------|
| `DECIDE` | 角色决策 |
| `PATHFAIL` | 寻路失败（含位置和次数） |
| `SELL/BUY` | 买卖（含物品/数量/金额） |
| `USE_VOUCHER/BUY_VOUCHER` | 升级券操作 |
| `TASK_CALL_LLM` | 自进化任务 LLM 调用 |
| `TASK_LLM_RESP` | LLM 返回内容 |
| `TREASURE_PARSED/BUY/MOVING/SUMMONED/WAITING` | 宝藏各阶段 |
| `RETURN_DEFEND` | 回防移动 |

---

## 十三、关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| 1 | 工人分工（石头/铜铁分离） | 围墙材料与金币双线供给 |
| 2 | 持续采集 | 减少空转移动 |
| 3 | 动态回防 + buffer | 适配不同采矿距离 |
| 4 | 方位感知 | 靠墙侧不建墙省石头；武器朝敌人 |
| 5 | 固定武器一人一座 | 夜晚不走路，人人有火力 |
| 6 | 武器间隔≥2 | 覆盖更大范围 |
| 7 | 卖矿优先于挖矿 | 形成金币循环→升级武器 |
| 8 | 官方新闻加成 | 涨价矿石优先采/卖 |
| 9 | executeCmd 沙盒闭环 | 自进化任务真正执行命令 |
| 10 | 传闻积累 | 多天综合分析宝藏线索 |
