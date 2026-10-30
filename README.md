# ride-weather 🚲

一个 [Claude Code](https://claude.com/claude-code) skill：**每周骑一次车**。

- 每晚查一次明天的天气，**本周还没骑 + 明天适合**才弹窗提醒，骑过了就安静
- 跟 Claude 说「今天骑车了」就记一笔，统计连续达标了几周
- 问「这周哪天能骑」，看未来 7 天每天的最佳时段

```
西安  本周还没骑
10-31 周六(明天)  ✅ 10:00–14:00  体感11~13° 风6km/h   [体感9~13° 降水概率最高78% 风13km/h PM2.5最高91]
11-01 周日(后天)  ✅ 13:00–18:00  体感11~12° 风8km/h   [体感10~12° 降水概率最高43% 风9km/h PM2.5最高64]
11-03 周二  ❌ 雨   [体感10~11° 降水概率最高42% 风7km/h PM2.5最高69]
```

天气数据来自 [Open-Meteo](https://open-meteo.com)（免费、免 key），只用 Python 标准库，不用装任何依赖。

## 判断标准

在可骑时段（默认周末 9–18 点、工作日 17–19 点）里，要有**连续 ≥3 小时**同时满足：

| 项 | 默认阈值 | 配置键 |
|---|---|---|
| 降水 | 概率 ≤30%，雨量 ≤0.2mm/h | `rain_prob_max` `rain_mm_max` |
| 体感温度 | 5–33°C | `feel_min` `feel_max` |
| 风 | ≤25km/h，阵风 ≤40km/h | `wind_max` `gust_max` |
| PM2.5 | ≤75 μg/m³ | `pm25_max` |
| 路面 | 出发前 12 小时雨量 ≤5mm | `wet_road_mm` |
| 连续时长 | ≥3 小时 | `min_block` |

## 安装

需要 Python 3.8+。

```bash
# 1. 放进 Claude Code 的 skills 目录
git clone https://github.com/jasonkyo/ride-weather ~/.claude/skills/ride-weather

# 2. 设置城市（中文英文都行）
python ~/.claude/skills/ride-weather/scripts/ride_check.py setup 西安

# 3. 看看效果
python ~/.claude/skills/ride-weather/scripts/ride_check.py week
```

### 每晚自动提醒

**Windows**（每天 20:30，`pythonw` 不会弹黑框）：

```powershell
schtasks /create /tn RideWeather /sc daily /st 20:30 /f /tr "\"<pythonw.exe 路径>\" \"%USERPROFILE%\.claude\skills\ride-weather\scripts\ride_check.py\" notify"
```

**macOS / Linux**（`crontab -e` 加一行）：

```
30 20 * * * /usr/bin/env python3 ~/.claude/skills/ride-weather/scripts/ride_check.py notify
```

弹窗方式：Windows 用置顶对话框（PowerShell toast 在 Windows 11 上经常被静默丢弃），macOS 用 `osascript`，Linux 用 `notify-send`。

## 自定义

在 `~/.ride-weather/config.json` 里只写要改的键，例如：

```json
{
  "weekday_hours": [15, 18],
  "feel_min": 8,
  "wind_max": 20
}
```

数据目录可以用环境变量 `RIDE_WEATHER_HOME` 改到别处。

## 命令

```
ride_check.py setup <城市>        设置城市
ride_check.py week              未来 7 天能不能骑
ride_check.py log [日期] [备注]  记一次骑行
ride_check.py status            本周骑了没、连续达标周数
ride_check.py notify            定时任务用
```

装好之后直接跟 Claude 说话就行：「今天骑车了，30 公里」「这周哪天能骑」「风的标准放宽点」。

## License

MIT
