---
name: ride-weather
description: 每周骑一次车的天气提醒和骑行记录。用户说"今天骑车了 / 骑完了 / 记一下骑行"、"这周哪天适合骑车 / 明天能骑吗 / 骑车天气"、"这周骑了没 / 骑行记录"、或者要调整骑车提醒（城市、时段、温度、风、雾霾标准）时使用。
---

# 每周骑车提醒

目标：**每周（周一到周日）至少骑一次**。每晚查一次明天的天气，本周还没骑、明天又适合，就弹窗提醒；骑过就不打扰。

## 组成
- 脚本：本 skill 目录下的 `scripts/ride_check.py`（只用 Python 标准库）
- 数据目录：`~/.ride-weather/`（环境变量 `RIDE_WEATHER_HOME` 可改）
  - `config.json`：城市坐标、时段、阈值，只写要覆盖的键，其余用脚本里的 `DEFAULT_CONFIG`
  - `rides.json`：骑行记录 `[{date, note}]`
  - `ride_check.log`：每次定时检查的判断结果
- 天气：[Open-Meteo](https://open-meteo.com) 逐小时预报 + 空气质量，免 key
- 定时任务：每天晚上跑 `notify`（装法见 README）

运行：`python scripts/ride_check.py <命令>`（Windows 定时任务用 `pythonw` 不弹黑框）

| 命令 | 用途 |
|---|---|
| `setup <城市>` | 设置城市，自动查坐标和时区 |
| `log [YYYY-MM-DD] [备注]` | 记一次骑行，日期省略=今天，同一天重复记会覆盖 |
| `week` | 今天起 7 天每天能不能骑、最佳时段 |
| `status` | 本周骑了没、连续达标周数、最近 10 次 |
| `notify` | 定时任务用；手动跑可测试 |

## "适合骑车"怎么判断
在可骑时段里（默认周末 9–18 点，工作日 17–19 点）找**连续 ≥3 小时**每小时都满足：
降水概率 ≤30% 且雨量 ≤0.2mm、体感 5–33°、风 ≤25km/h 且阵风 ≤40、PM2.5 ≤75；
并且出发前 12 小时累计雨量 ≤5mm（否则路湿）。

## 怎么用
- **第一次用 / 报 "还没设置城市"**：问用户在哪个城市，跑 `setup <城市>`，再问工作日几点能骑，写进 `config.json` 的 `weekday_hours`。
- **用户说骑了**：跑 `log`，备注写里程/路线/感受（用户说了才写），再跑 `status` 告诉用户本周达标了、连续几周。
- **问哪天能骑**：跑 `week`，用一两句话给建议（哪天最好、几点），不要把整张表原样丢过去。
- **改标准**：改 `~/.ride-weather/config.json`（只写要改的键），再跑 `week` 确认效果。
- **没收到提醒**：先看 `ride_check.log`，再查定时任务（Windows `schtasks /query /tn RideWeather /v`，mac/Linux `crontab -l`）。
