# 最终版开发文档 — 未来战争参赛 AI

> 版本：v2.0（融合代码实现 + 决策规范）
> 日期：2026-09-22

---

## 一、代码架构

```
CoreGeek/
├── main3.py          # 入口：python main3.py <port>
├── pyproject.toml    # 项目配置
└── src/agent/
    ├── __init__.py   # 公共接口导出
    ├── server.py     # HTTP 服务（ThreadingHTTPServer）
    ├── protocol.py   # 数据层：常量 / dataclass / 指令构造
    ├── brain.py      # 决策核心：decide() → roleCommandMap
    └── grid.py       # A* 寻路：next_step()
```

### 数据流

```
判题器 POST JSON → server.py → brain.decide(payload) → JSON response
                                                    ↓
                                              roleCommandMap
                                              prompt（LLM）
                                              executeCmd（沙盒）
```

---

## 二、Request 解析（protocol.py）

### 2.1 Turn 数据结构

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `round_no` | int | `roundNo` | 当前回合数 |
| `is_day` | bool | 计算 | `(round_no-1) % 130 < 70` |
| `day_number` | int | 计算 | `(round_no-1) // 130 + 1` |
| `gold` | int | `teamOur.goldNum` | 当前金币 |
| `width/height` | int | `mapInfo` | 41 × 32 |
| `zones` | dict | `mapInfo.zones` | Pos → neutralType 映射 |
| `ours` | tuple | `teamOur.roles` | 我方全部单位 |
| `robots` | tuple | `robot.roles` | 场上全部机器人 |
| `vendor_prices` | dict | `vendorShopList` | 矿石收购价 |
| `weapon_shop` | list | `weaponShopList` | 商品清单与价格 |
| `world_news` | dict | `worldNews` | 官方消息 + 民间传闻 |
| `phase_task` | str | `phaseTask` | 当前已领任务原文 |
| `player_tasks` | tuple | `teamOur.playerTasks` | 任务点列表 |
| `llm_resp` | str | `llmResp` | 上回合 LLM 返回 |
| `errors` | list | `errors` | 本轮错误 |
| `last_action_results` | dict | `lastRoundRoleActionResults` | 上回合指令合法性 |
| `team_type` | str | `teamOur.type` | challenger / defender |

### 2.2 Unit 数据结构

| 字段 | 说明 |
|------|------|
| `unit_id` | 角色唯一 ID（worker: 10010/10012, pioneer: 10011, station: 10013） |
| `pos` | 坐标 Pos(x, y) |
| `kind` | roleType：station/gatling/railgun/rocket/wall/worker/pioneer |
| `health` | 当前血量 |
| `level` | 建筑等级（1-3），角色无等级 |
| `cooldown` | 武器冷却（仅火箭有） |
| `attack_range` | 攻击距离 |
| `capacity` | 背包容量 |
| `backpack` | 物品元组 |

### 2.3 PlayerTask 数据结构

| 字段 | 说明 |
|------|------|
| `task_type` | 自进化类1 / 自进化类2 |
| `task_position` | 任务点坐标 |
| `cold_down_rounds` | 冷却剩余回合（0=可接取） |
| `score_reward` | 积分奖励 |
| `gold_reward` | 金币奖励 |
| `is_valid` | 是否可接取 |
| `timeout_rounds` | 超时回合数 |

---

## 三、常量定义

```python
DAY_ROUNDS = 70          # 白天回合数
NIGHT_ROUNDS = 60        # 夜晚回合数
ROUNDS_PER_DAY = 130     # 每天总回合
MAX_ROUNDS = 1300        # 比赛最大回合

WEAPON_BUILD_COST = 25   # 建造武器花费金币
WALL_MATERIAL = "stone"  # 围墙材料

TOWER_RANGE_BY_LEVEL = {
    "gatling": (3, 5, 7),
    "railgun": (6, 8, 10),
    "rocket": (10, 15, 10**9),
}
TOWER_MULTI_TARGET = {
    "gatling": (1, 2, 3),   # 每等级可攻击目标数
    "rocket": (1, 2, 3),
    "railgun": (1, 1, 1),   # 始终单目标
}
ROCKET_COOLDOWN = 3         # 火箭发射冷却回合

ROBOT_STATS = {
    "smallRobot":  {"hp": 40,  "atk": 5,  "range": 3, "score": 1},
    "middleRobot": {"hp": 60,  "atk": 10, "range": 3, "score": 2},
    "largeRobot":  {"hp": 500, "atk": 20, "range": 3, "score": 4},
    "bossRobot":   {"hp": 800, "atk": 40, "range": 3, "score": 10},
}
ROBOT_THREAT_PRIORITY = {
    "bossRobot": 4, "largeRobot": 3, "middleRobot": 2, "smallRobot": 1,
}
```

---

## 四、决策核心（brain.py）

### 4.1 主流程 decide()

```
输入: payload (JSON)
  ↓
Turn.load(payload) — 解析为结构化数据
  ↓
判断 is_day → _day() / _night()
  ↓
收集 LLM prompt（来自任务执行）
  ↓
输出: {roleCommandMap, prompt, executeCmd}
```

### 4.2 白天策略 `_day()`

#### 工人1（建造工）— 前2天

| 优先级 | 动作 | 条件 |
|--------|------|------|
| 1 | 建造炮台（gatling→railgun→rocket） | 有空位且金币≥25 |
| 2 | 采石头 | 无空位时 |

#### 工人1（建造工）— 第3天起

| 优先级 | 动作 | 条件 |
|--------|------|------|
| 1 | 建造缺失炮台 | 有空位且金币≥25 |
| 2 | 贩卖矿石 | 背包≥60%满 |
| 3 | 采石头 | 石头<10 |
| 4 | 采铁矿 | 铁<5 |
| 5 | 采铜矿 | 铜<5 |
| 6 | 采石头 | 兜底 |

#### 工人2（经济工）

| 优先级 | 动作 | 条件 |
|--------|------|------|
| 1 | 贩卖矿石 | 背包非空 |
| 2 | 使用升级券 | 背包有券且角色在建筑1格内 |
| 3 | 购买升级用品 | 金币≥100且第3天起 |
| 4 | 建造围墙 | 有石头且有空位 |
| 5 | 采石头 | 兜底 |

#### 购买优先级 `_buy_priority()`

1. 武器升级券（level1→2: 100金, level2→3: 150金）
2. 基地升级券（同上价格）
3. 眩晕法宝/范围炸弹（夜晚且回合余量>50时）
4. 围墙修复包（有低血量围墙时）
5. 生命药剂（有低血量角色时）

#### 升级券使用 `_try_use_upgrade_vouchers()`

白天自动检测背包中的券，**需要角色在目标建筑1格内**才能使用：
- Medicine → 血量<200时使用
- WeaponUpgradeVoucher1/2 → 对应等级武器
- StationUpgradeVoucher1/2 → 对应等级基地
- WallUpgradeVoucher1/2 → 对应等级围墙
- WallFixer → 血量<800的围墙

### 4.3 开拓者策略

#### 三级优先级

```
1. 有已领任务（phase_task非空）→ 执行任务
2. 有可接任务（valid_tasks非空）→ 前往领取
3. 空闲 → 宝藏召唤 + 购买用品 + 巡逻
```

#### 任务执行 `_pioneer_do_task()`

```
if Medicine in backpack and health < 110:
    → use Medicine（保命优先）
elif llm_resp 非空（上回合LLM已回答）:
    → submitAnswer(llm_resp)
else:
    → 发送 prompt 请求 LLM 回答（任务期间不限次数）
```

#### 领取任务 `_pioneer_go_task()`

- 选择奖励最高（score_reward, gold_reward）的有效任务
- 走到任务点1格内 → `acceptTask`

#### 空闲行为 `_pioneer_idle()`

```
0. 尝试宝藏召唤
1. 购买任务用品（6种各15金）
2. 往任务点方向巡逻
3. 回基地附近
```

### 4.4 夜晚策略 `_night()`

#### 角色分配

```
 所有角色先使用消耗品保命
② 工人优先配对最近未分配武器
③ 开拓者配对剩余武器（无武器则回安全位置）
④ 未配对角色前往最近武器
```

#### 武器操作 `_operate_or_approach()`

- 角色在武器1格内 → 选择目标攻击
- 角色不在武器旁 → 走向武器
- 火箭冷却中 → 跳过

#### 消耗品使用 `_role_use_items_night()`

| 优先级 | 物品 | 触发条件 |
|--------|------|---------|
| 1 | Medicine | 背包有药 + 血量<110 |
| 2 | Bomb | 背包有炸弹 + 有机器人 |
| 3 | DizzyWeapon | 背包有眩晕 + 有机器人 |

#### 攻击目标选择 `_select_attack_targets()`

**通用排序**：按威胁度降序 → 距基地距离升序 → 血量升序

| 武器 | 策略 |
|------|------|
| **加特林** | 90°锥形约束，选同方向最多n个目标，每发10伤害 |
| **电磁炮** | 遍历每个候选目标，计算弹道穿透总伤害，选最大 |
| **火箭** | 3×3区域去重，选覆盖不同区域的n个目标，中心20+溅射10 |

---

## 五、路径寻路（grid.py）

A* 算法，切比雪夫距离为启发函数：

```python
next_step(turn, moving_unit, goal_pos) → Pos | None
```

- 8方向移动
- 避开障碍（zones非land、建筑、角色、机器人）
- 返回第一步坐标（非完整路径）

---

## 六、指令全集

### 6.1 动作码与限制

| 动作 | 可用角色 | 时间 | 距离要求 |
|------|---------|------|---------|
| `move` | 全部 | 全天 | 目标1格内 |
| `attack` | 全部（操控武器） | **仅夜晚** | 武器射程内 |
| `collect` | **仅工人** | 全天 | 矿点1格内 |
| `build` | **仅工人** | **仅白天** | 目标1格内 |
| `remove` | **仅工人** | 全天 | 围墙1格内 |
| `sell` | 全部 | 全天 | 小贩1格内 |
| `buy` | 全部 | 全天 | 武器商店1格内 |
| `use` | 全部 | 全天 | 部分需指定位置 |
| `acceptTask` | **仅开拓者** | 全天 | 任务点1格内 |
| `submitAnswer` | **仅开拓者** | 全天 | 无 |
| `summonTreasure` | **仅开拓者** | 全天 | 祭坛1格内 |
| `drop` | 全部 | 全天 | 无 |

### 6.2 指令构造

```python
move_command(pos)
collect_command(pos)
build_command(pos, name)
remove_command(pos)
attack_command(controller_id, [target_pos, ...])
sell_command(name, num)
buy_command(name, num)
use_command(name, target_pos=None)
drop_command(name)
accept_task_command()
submit_answer_command(answer)
summon_treasure_command(target_pos, [items])
```

---

## 七、积分规则

```
总积分 = score_1 + score_2 + score_3

score_1（任务）= 任务积分 + 5 × 标准回合数 / (实际回合 - 接取回合)
score_1（部分）= 任务积分 × 通过率

score_2（击杀）= Σ(击杀数 × 机器人积分)

score_3（生存）= Σ(day=1→10) 10 × day × 存活系数
  存活系数 = 1（基地存活）/ 0（基地被毁当天及之后）
```

---

## 八、胜负判定

1. **结束条件**：双方异常≥5次 / 1300回合结束 / 双方基地均被毁
2. **半场胜负**：基地先被毁者负 / 积分高者胜 / 同时被毁且积分相同则平
3. **全场**：两半场各计胜3/平1/负0分，总和高者胜

---

## 九、异常处理

| 异常类型 | 计数 | 后果 |
|---------|------|------|
| 响应超时 | 连接>10s或响应>5s | 累计5次后不再调度 |
| 格式错误 | 返回JSON不合规 | 同上 |
| 指令错误 | 字段缺失或无法识别 | 同上 |

> 指令执行失败（如碰撞、落点无目标）**不计入**异常次数。

---

## 十、代码实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| HTTP 服务 | ✅ 完成 | server.py |
| 数据结构解析 | ✅ 完成 | protocol.py |
| A* 寻路 | ✅ 完成 | grid.py |
| 白天建造策略 | ✅ 完成 | brain.py |
| 白天采矿/贩卖 | ✅ 完成 | brain.py |
| 白天购买策略 | ✅ 完成 | brain.py |
| 升级券自动使用 | ✅ 完成 | brain.py `_try_use_upgrade_vouchers()` |
| 开拓者任务系统 | ✅ 完成 | brain.py（LLM闭环） |
| 开拓者领取任务 | ✅ 完成 | brain.py `_pioneer_go_task()` |
| 开拓者空闲巡逻 | ✅ 完成 | brain.py `_pioneer_idle()` |
| 夜晚角色分配 | ✅ 完成 | brain.py `_night()` 工人优先 |
| 加特林锥形攻击 | ✅ 完成 | brain.py `_gatling_targets()` |
| 电磁炮穿透攻击 | ✅ 完成 | brain.py `_best_railgun_target()` |
| 火箭区域攻击 | ✅ 完成 | brain.py `_rocket_targets()` |
| 夜晚消耗品使用 | ✅ 完成 | brain.py `_role_use_items_night()` |
| LLM 调用闭环 | ✅ 完成 | prompt → llmResp → submitAnswer |
| 宝藏召唤 | ✅ 完成 | brain.py `_try_summon_treasure()` LLM解析传闻 |
| 上回合失败重试 | ✅ 完成 | brain.py `_retry_hint()` 生成LLM重试提示 |
| executeCmd 沙盒 | ❌ 未实现 | 自进化任务备选方案（LLM闭环已可覆盖） |
| 官方新闻分析 | ✅ 完成 | brain.py `_analyze_ore_news()` 采矿+贩卖优先级调整 |
| 机器人召唤令 | ✅ 完成 | brain.py `_try_buy_summon_orders()` 购买 + 夜晚首回合使用 |
| LLM 次数限制 | ✅ 完成 | brain.py `_can_use_llm()` / `_record_llm_call()` 每游戏日3次 |
