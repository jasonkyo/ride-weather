"""每周骑一次车：查天气，本周还没骑且明天天气好就弹窗提醒。

用法：
  ride_check.py setup <城市>        设置城市（自动查坐标和时区），如 setup 西安
  ride_check.py notify            每晚定时任务跑：本周没骑 + 明天适合 → 弹窗
  ride_check.py notify --preview  不管骑没骑，弹一下看效果
  ride_check.py skip              这周不骑了，本周不再提醒
  ride_check.py week              打印今天起 7 天每天能不能骑
  ride_check.py log [日期] [备注]  记一次骑行（日期默认今天，格式 2026-10-30）
  ride_check.py status            本周骑了没、连续达标周数、最近记录

数据目录默认 ~/.ride-weather，可用环境变量 RIDE_WEATHER_HOME 改。
"""
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

if sys.stdout is None:  # Windows pythonw 下没有控制台
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DIR = os.environ.get("RIDE_WEATHER_HOME") or os.path.join(os.path.expanduser("~"), ".ride-weather")
RIDES = os.path.join(DIR, "rides.json")
LOG = os.path.join(DIR, "ride_check.log")
CONFIG = os.path.join(DIR, "config.json")
SKIPS = os.path.join(DIR, "skips.json")

DEFAULT_CONFIG = {
    "city": None, "lat": None, "lon": None,
    "timezone": "auto",
    "weekend_hours": [9, 18],   # 周末可骑时段
    "weekday_hours": [17, 19],  # 工作日下班后
    "min_block": 3,             # 至少连续几个小时天气都行
    "rain_prob_max": 30,        # 降水概率 %
    "rain_mm_max": 0.2,         # 每小时降水量 mm
    "feel_min": 5, "feel_max": 33,   # 体感温度 °C
    "wind_max": 25, "gust_max": 40,  # km/h
    "pm25_max": 75,             # μg/m³，超过算污染（国标轻度污染线）
    "wet_road_mm": 5,           # 出发前 12 小时累计雨量超过这个算路湿
}

WEEKDAY = "一二三四五六日"


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if cfg["lat"] is None or cfg["lon"] is None:
        sys.exit("还没设置城市，先运行：ride_check.py setup <城市>")
    return cfg


def save_json(path, obj):
    os.makedirs(DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def log(msg):
    os.makedirs(DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}\n")


def load_rides():
    if not os.path.exists(RIDES):
        return []
    with open(RIDES, encoding="utf-8") as f:
        return json.load(f)


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def rode_in_week(rides, d):
    ws = week_start(d)
    return [r for r in rides if week_start(dt.date.fromisoformat(r["date"])) == ws]


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def fetch_forecast(cfg):
    loc = (f"?latitude={cfg['lat']}&longitude={cfg['lon']}"
           f"&timezone={urllib.parse.quote(cfg['timezone'])}")
    h = get_json("https://api.open-meteo.com/v1/forecast" + loc +
                 "&hourly=apparent_temperature,precipitation_probability,precipitation,"
                 "wind_speed_10m,wind_gusts_10m&past_days=1&forecast_days=8")["hourly"]
    try:  # 空气质量预报只有 5 天左右，拿不到就不管
        a = get_json("https://air-quality-api.open-meteo.com/v1/air-quality" + loc +
                     "&hourly=pm2_5&past_days=1&forecast_days=7")["hourly"]
        pm = dict(zip(a["time"], a["pm2_5"]))
    except Exception as e:
        log(f"空气质量取不到：{e!r}")
        pm = {}
    return [{
        "t": dt.datetime.fromisoformat(t),
        "feel": h["apparent_temperature"][i],
        "pp": h["precipitation_probability"][i] or 0, "mm": h["precipitation"][i] or 0,
        "wind": h["wind_speed_10m"][i] or 0, "gust": h["wind_gusts_10m"][i] or 0,
        "pm": pm.get(t),
    } for i, t in enumerate(h["time"])]


def hour_problems(x, cfg):
    bad = []
    if x["pp"] > cfg["rain_prob_max"] or x["mm"] > cfg["rain_mm_max"]:
        bad.append("雨")
    if x["feel"] is not None and x["feel"] > cfg["feel_max"]:
        bad.append("热")
    if x["feel"] is not None and x["feel"] < cfg["feel_min"]:
        bad.append("冷")
    if x["wind"] > cfg["wind_max"] or x["gust"] > cfg["gust_max"]:
        bad.append("风大")
    if x["pm"] is not None and x["pm"] > cfg["pm25_max"]:
        bad.append("雾霾")
    return bad


def judge_day(day, hours, cfg):
    """看这一天可骑时段里有没有足够长的连续好天气。"""
    lo, hi = cfg["weekend_hours"] if day.weekday() >= 5 else cfg["weekday_hours"]
    seg = [x for x in hours if x["t"].date() == day and lo <= x["t"].hour < hi]
    if not seg:
        return {"ok": False, "reason": "没有预报"}
    feel = [x["feel"] for x in seg if x["feel"] is not None]
    pms = [x["pm"] for x in seg if x["pm"] is not None]
    summary = (f"体感{min(feel):.0f}~{max(feel):.0f}° 降水概率最高{max(x['pp'] for x in seg)}% "
               f"风{max(x['wind'] for x in seg):.0f}km/h"
               + (f" PM2.5最高{max(pms):.0f}" if pms else ""))
    start = dt.datetime.combine(day, dt.time(lo))
    rain12 = sum(x["mm"] for x in hours if start - dt.timedelta(hours=12) <= x["t"] < start)
    if rain12 > cfg["wet_road_mm"]:
        return {"ok": False, "reason": f"之前下雨{rain12:.0f}mm，路湿", "summary": summary}
    probs = [hour_problems(x, cfg) for x in seg]
    best, cur = (0, 0), None
    for i, p in enumerate(probs + [["end"]]):
        if not p:
            cur = i if cur is None else cur
        elif cur is not None:
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    if best[1] - best[0] < cfg["min_block"]:
        reasons = sorted({b for p in probs for b in p})
        return {"ok": False, "reason": "、".join(reasons) or "好天气时段太短", "summary": summary}
    w = seg[best[0]:best[1]]
    wpm = [x["pm"] for x in w if x["pm"] is not None]
    # 好时段一结束就下雨的话要提醒：别骑远了回不来
    end = w[-1]["t"] + dt.timedelta(hours=1)
    after = [x for x in hours if end <= x["t"] < end + dt.timedelta(hours=3)]
    wet = [x for x in after if x["pp"] > cfg["rain_prob_max"] or x["mm"] > cfg["rain_mm_max"]]
    warn = (f"{wet[0]['t'].hour}点后要下雨（降水概率升到{max(x['pp'] for x in after)}%），"
            f"{end.hour}点前回来") if wet else None
    return {"ok": True, "summary": summary,
            "window": f"{w[0]['t'].hour}:00–{w[-1]['t'].hour + 1}:00",
            "detail": f"体感{min(x['feel'] for x in w):.0f}~{max(x['feel'] for x in w):.0f}° "
                      f"风{max(x['wind'] for x in w):.0f}km/h" + (f"，⚠ {warn}" if warn else ""),
            "warn": warn,
            # 下面这些给弹窗画图用
            "feel": f"{min(x['feel'] for x in w):.0f}~{max(x['feel'] for x in w):.0f}°",
            "wind": f"{max(x['wind'] for x in w):.0f}",
            "pp": max(x["pp"] for x in w),
            "pm": f"{max(wpm):.0f}" if wpm else None,
            "best": list(best),
            "hours": [{"h": x["t"].hour, "feel": x["feel"], "bad": p} for x, p in zip(seg, probs)]}


def day_label(d, today):
    rel = {0: "今天", 1: "明天", 2: "后天"}.get((d - today).days, "")
    return f"{d:%m-%d} 周{WEEKDAY[d.weekday()]}" + (f"({rel})" if rel else "")


def popup(card):
    """弹提醒卡片（popup_ui.py，tkinter），在单独进程里弹，不卡主脚本。"""
    exe = sys.executable
    if platform.system() == "Windows":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.exists(w) else exe
    ui = os.path.join(os.path.dirname(os.path.abspath(__file__)), "popup_ui.py")
    subprocess.Popen([exe, ui, json.dumps(card, ensure_ascii=False)])


def plain_popup(title, body):
    """没有 tkinter 时的退路。Windows 用置顶 MessageBox（PowerShell toast 常被系统静默丢弃），
    macOS 用 osascript，Linux 用 notify-send。"""
    system = platform.system()
    if system == "Windows":
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        code = ("import sys, ctypes; "
                "ctypes.windll.user32.MessageBoxW(0, sys.argv[2], sys.argv[1], 0x40 | 0x1000 | 0x10000 | 0x40000)")
        subprocess.Popen([exe if os.path.exists(exe) else sys.executable, "-c", code, title, body])
    elif system == "Darwin":
        script = 'on run argv\ndisplay dialog (item 2 of argv) with title (item 1 of argv) buttons {"好"}\nend run'
        subprocess.Popen(["osascript", "-e", script, title, body])
    elif shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-u", "critical", title, body])
    else:
        print(f"{title}\n{body}")


def cmd_setup(args):
    if not args:
        sys.exit("用法：ride_check.py setup <城市>")
    name = " ".join(args)
    res = get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=zh&name="
                   + urllib.parse.quote(name)).get("results")
    if not res:
        sys.exit(f"找不到城市：{name}")
    r = res[0]
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.update(city=r["name"], lat=round(r["latitude"], 2), lon=round(r["longitude"], 2),
               timezone=r.get("timezone", "auto"))
    save_json(CONFIG, cfg)
    print(f"已设置：{r['name']}（{r.get('admin1', '')} {r.get('country', '')}）"
          f" {cfg['lat']},{cfg['lon']} {cfg['timezone']}\n配置文件：{CONFIG}")


def cmd_week(args):
    cfg = load_config()
    today = dt.date.today()
    hours = fetch_forecast(cfg)
    done = rode_in_week(load_rides(), today)
    print(f"{cfg['city']}  本周" + ("已骑 " + ", ".join(r["date"] for r in done) if done else "还没骑"))
    for i in range(7):
        d = today + dt.timedelta(days=i)
        j = judge_day(d, hours, cfg)
        mark = f"✅ {j['window']}  {j['detail']}" if j["ok"] else f"❌ {j['reason']}"
        print(f"{day_label(d, today)}  {mark}   [{j.get('summary', '')}]")


def load_skips():
    if not os.path.exists(SKIPS):
        return []
    with open(SKIPS, encoding="utf-8") as f:
        return json.load(f)


def cmd_skip(args):
    """这周不骑了，本周不再提醒。参数可给本周任意一天，默认明天所在的周。"""
    d = dt.date.fromisoformat(args[0]) if args else dt.date.today() + dt.timedelta(days=1)
    ws = week_start(d).isoformat()
    skips = sorted(set(load_skips()) | {ws})
    save_json(SKIPS, skips)
    print(f"{ws} 这周不再提醒")


def cmd_notify(args):
    """--preview：不管本周骑没骑，弹一下看效果（明天不适合就挑 7 天内第一个好天）。"""
    preview = "--preview" in args
    cfg = load_config()
    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)
    if not preview:
        if rode_in_week(load_rides(), tomorrow):
            log("本周已骑，不提醒")
            return
        if week_start(tomorrow).isoformat() in load_skips():
            log("这周说了不骑，不提醒")
            return
    hours = fetch_forecast(cfg)
    day, j = tomorrow, judge_day(tomorrow, hours, cfg)
    if not j["ok"]:
        if not preview:
            log(f"明天不适合：{j['reason']}")
            return
        for i in range(7):
            day = today + dt.timedelta(days=i)
            j = judge_day(day, hours, cfg)
            if j["ok"]:
                break
        else:
            print("7 天内都没有适合骑车的天")
            return
    rel = {0: "今天", 1: "明天", 2: "后天"}.get((day - today).days, f"周{WEEKDAY[day.weekday()]}")
    others = []
    d = day + dt.timedelta(days=1)
    while week_start(d) == week_start(day):
        jj = judge_day(d, hours, cfg)
        if jj["ok"]:
            others.append(f"周{WEEKDAY[d.weekday()]} {jj['window']}")
        d += dt.timedelta(days=1)
    if day.weekday() == 6:
        tail = f"{rel}是本周最后一天，这周还没骑！"
    elif others:
        tail = "本周后面也行：" + "、".join(others)
    else:
        tail = f"本周后面天气都不太行，就{rel}吧"
    body = (f"{cfg['city']} {rel} 周{WEEKDAY[day.weekday()]} {j['window']} 适合骑车\n"
            f"{j['detail']}（全天 {j['summary']}）\n\n{tail}\n\n"
            "骑完跟 Claude 说一声「今天骑车了」")
    popup({
        "title": f"{rel}适合骑车", "body": body, "rel": rel,
        "sub": f"{cfg['city']} · {day.month}月{day.day}日 周{WEEKDAY[day.weekday()]}",
        "window": j["window"], "feel": j["feel"], "wind": j["wind"], "pp": j["pp"], "pm": j["pm"],
        "hours": j["hours"], "best": j["best"], "tail": tail, "day": day.isoformat(),
        "warn": j["warn"],
    })
    if not preview:
        log(f"已提醒：{rel} {j['window']} {j['detail']}")


def cmd_log(args):
    has_date = bool(args) and args[0][:2] == "20"
    d = dt.date.fromisoformat(args[0]) if has_date else dt.date.today()
    note = " ".join(args[1:] if has_date else args)
    rides = [r for r in load_rides() if r["date"] != d.isoformat()]
    rides.append({"date": d.isoformat(), "note": note})
    rides.sort(key=lambda r: r["date"])
    save_json(RIDES, rides)
    print(f"已记录 {d} 周{WEEKDAY[d.weekday()]} {note}")


def cmd_status(args):
    rides = load_rides()
    today = dt.date.today()
    done = rode_in_week(rides, today)
    print("本周：" + ("已骑 " + ", ".join(r["date"] for r in done) if done else "还没骑"))
    weeks = {week_start(dt.date.fromisoformat(r["date"])) for r in rides}
    w = week_start(today) if done else week_start(today) - dt.timedelta(days=7)
    streak = 0
    while w in weeks:
        streak, w = streak + 1, w - dt.timedelta(days=7)
    print(f"连续 {streak} 周达标，共骑 {len(rides)} 次")
    for r in rides[-10:]:
        print(f"  {r['date']}  {r.get('note', '')}")


if __name__ == "__main__":
    a = sys.argv[1:] or ["week"]
    cmds = {"setup": cmd_setup, "notify": cmd_notify, "week": cmd_week,
            "log": cmd_log, "status": cmd_status, "skip": cmd_skip}
    if a[0] not in cmds:
        sys.exit(__doc__)
    try:
        cmds[a[0]](a[1:])
    except Exception as e:
        log(f"出错：{e!r}")
        raise
