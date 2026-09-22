---
name: coregeek-project-status
description: 未来战争参赛AI项目当前状态与实现进度
metadata:
  type: project
---

## 最新状态 (2026-09-22)

### 项目概况
华为云核心网第十届编程大赛 — 未来战争参赛AI
- 语言：Python 3.11+，零外部依赖
- 架构：HTTP 无状态服务，每回合收状态→决策→返回指令
- 地图：41×32，1300回合（10天×130回合/天）

### 全部功能实现状态

| # | 功能 | 状态 | 文件/函数 |
|---|------|------|-----------|
| 1 | HTTP 服务 | ✅ | `server.py` |
| 2 | 数据结构解析 | ✅ | `protocol.py` (Pos/Unit/Robot/PlayerTask/Turn) |
| 3 | A* 寻路 | ✅ | `grid.py` `next_step()` |
| 4 | 白天建造策略 | ✅ | `brain.py` `_worker_build_tower()` |
| 5 | 白天采矿/贩卖 | ✅ | `brain.py` `_worker_miner()` `_go_sell()` |
| 6 | 白天购买策略 | ✅ | `brain.py` `_buy_priority()` |
| 7 | 升级券自动使用 | ✅ | `brain.py` `_try_use_upgrade_vouchers()` |
| 8 | 开拓者任务(LLM闭环) | ✅ | `brain.py` `_pioneer_do_task()` |
| 9 | 开拓者领取任务 | ✅ | `brain.py` `_pioneer_go_task()` |
| 10 | 开拓者空闲巡逻 | ✅ | `brain.py` `_pioneer_idle()` |
| 11 | 宝藏召唤(LLM解析传闻) | ✅ | `brain.py` `_try_summon_treasure()` |
| 12 | 夜晚角色分配(工人优先) | ✅ | `brain.py` `_night()` |
| 13 | 加特林锥形攻击 | ✅ | `brain.py` `_gatling_targets()` |
| 14 | 电磁炮穿透攻击 | ✅ | `brain.py` `_best_railgun_target()` |
| 15 | 火箭区域攻击 | ✅ | `brain.py` `_rocket_targets()` |
| 16 | 夜晚消耗品自动使用 | ✅ | `brain.py` `_role_use_items_night()` |
| 17 | 官方新闻分析 | ✅ | `brain.py` `_analyze_ore_news()` |
| 18 | 机器人召唤令 | ✅ | `brain.py` `_try_buy_summon_orders()` |
| 19 | LLM 调用计数(每游戏日3次) | ✅ | `brain.py` `_can_use_llm()` `_record_llm_call()` |
| 20 | 上回合失败自动重试 | ✅ | `brain.py` `_auto_retry()` `_retry_hint()` |

### 尚未实现
- `executeCmd` 沙盒命令（LLM闭环已可覆盖自进化任务）

### 待打包
- 生成 `CoreGeek.tar.gz` 上传判题平台
- 注意：必须用 tar 命令或 7zip 两次压缩，不能用 winRAR
