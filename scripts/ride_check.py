"""每周骑一次车：每晚弹一次，告诉你明天适不适合骑车/练单杠，以及本周骑了几次、练了几次。

本周骑了几次 = rides.json（手动 log / 卡片上点「今天骑了」）∪ 训记里的骑行（苹果健康同步的 Cycling）；
练了几次 = 训记里有非有氧动作的训练（单杠、力量）。训记可选：本机装了 xunji skill 才读，Key 从它的 SKILL.md 里取。

用法：
  ride_check.py setup <城市>        设置城市（自动查坐标和时区），如 setup 西安
  ride_check.py notify            每晚定时任务跑：弹卡片（这周点了「不骑了」就不弹）
  ride_check.py notify --preview  不管骑没骑，弹一下看效果
  ride_check.py skip              这周不骑了，本周不再提醒
  ride_check.py week              打印今天起 7 天每天能不能骑
  ride_check.py log [日期] [备注]  记一次骑行（日期默认今天，格式 2026-10-30）
  ride_check.py train [日期] [内容] 训记里没记的训练手动补一条
  ride_check.py plan [日期] [ride|train]  记下要去骑车/练单杠的日期（默认明天骑车），第二天晚上卡片核对
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
import time
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
TRAINS = os.path.join(DIR, "trains.json")   # 训记里没记的训练，卡片上点「今天练了」补的
PLANS = os.path.join(DIR, "plans.json")     # {日期: "ride"}，卡片上点「好，明天去骑」记下，第二天晚上核对

DEFAULT_CONFIG = {
    "city": None, "lat": None, "lon": None,
    "timezone": "auto",
    "weekend_hours": [9, 18],   # 周末可骑时段
    "weekday_hours": [17, 19],  # 工作日下班后
    "day_hours": None,          # 设了就不分工作日周末，比如 [9, 20]：卡片显示并在这段里找最佳时段
    "min_block": 3,             # 至少连续几个小时天气都行
    "rain_prob_max": 30,        # 降水概率 %
    "rain_mm_max": 0.2,         # 每小时降水量 mm
    "feel_min": 5, "feel_max": 33,   # 体感温度 °C
    "wind_max": 25, "gust_max": 40,  # km/h
    "pm25_max": 75,             # μg/m³，超过算污染（国标轻度污染线）
    "wet_road_mm": 5,           # 出发前 12 小时累计雨量超过这个算路湿
    # 红档（明显不适合）；黄档 = 超过上面的普通线但没到红线
    "heavy_rain_mm": 1.0, "heavy_rain_prob": 70,
    "wind_red": 35, "gust_red": 55,
    "pm25_red": 115,            # 国标中度污染线
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


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_rides():
    return load_json(RIDES, [])


def add_record(path, d, note=""):
    """rides.json / trains.json 记一条（同一天覆盖，不会重复）。"""
    recs = [r for r in load_json(path, []) if r["date"] != d.isoformat()]
    recs.append({"date": d.isoformat(), "note": note})
    recs.sort(key=lambda r: r["date"])
    save_json(path, recs)


def set_plan(d, what="ride"):
    plans = load_json(PLANS, {})
    plans[d.isoformat()] = what
    save_json(PLANS, plans)


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def rode_in_week(rides, d):
    ws = week_start(d)
    return [r for r in rides if week_start(dt.date.fromisoformat(r["date"])) == ws]


XUNJI_SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills", "xunji", "SKILL.md")


def xunji_week(day, upto, start=None):
    """训记里 start（默认 day 所在周的周一）~ upto 的骑行日和训练日。没装训记或读失败就返回空，不影响提醒。
    返回 (骑行日期集合, {训练日期: 训练标题})；有哪天没读到（没装、限频、断网），训练返回 None，别误报"还没练"。"""
    import gzip
    import re
    rides, trains = set(), {}
    try:
        key = re.search(r"Bearer (xjllm_\w+)", open(XUNJI_SKILL, encoding="utf-8").read()).group(1)
    except (OSError, AttributeError):
        return rides, None
    failed = False
    def fetch(d):
        """同一天 15 秒内只能读一次；别的程序（日程总结、复盘弹窗）刚读过时会回 too frequent，按提示等一下再读。"""
        for _ in range(3):
            req = urllib.request.Request(
                "https://trains.xunjiapp.cn/api_trains_for_llm_v2",
                json.dumps({"schema_version": "train_open_api_v2", "datestr": d.isoformat(),
                            "include_full_data": False}).encode(),
                {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept-Encoding": "gzip"})
            raw = urllib.request.urlopen(req, timeout=15).read()
            res = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).get("res")
            if isinstance(res, dict):
                return res
            wait = re.search(r"retry after (\d+)", str(res))
            if not wait:
                break
            time.sleep(min(int(wait.group(1)), 20) + 1)
        raise RuntimeError(str(res)[:80])  # 限频等错误时 res 是一句文字

    # 本机有 daily-summary 的训记统一缓存就走缓存（和日程总结、复盘弹窗共用，避免同时请求被限频）；没有就直接请求
    cache = None
    cache_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills", "daily-summary", "scripts")
    if os.path.exists(os.path.join(cache_dir, "xunji_cache.py")):
        sys.path.insert(0, cache_dir)
        try:
            import xunji_cache as cache
        except Exception:  # noqa: BLE001
            cache = None

    d = start or week_start(day)
    while d <= upto:
        try:
            res = {"trains": cache.trains(d)} if cache else fetch(d)
            for t in res.get("trains") or []:
                moves = t.get("movements") or []
                if any(m.get("name") == "Cycling" or "骑" in (m.get("name") or "") for m in moves):
                    rides.add(d)
                if any(m.get("exetype") != "cardio" for m in moves):  # 单杠、力量（走路骑车都是 cardio）
                    trains.setdefault(d, t.get("title") or "训练")
        except Exception as e:  # noqa: BLE001
            failed = True
            log(f"训记 {d} 读取失败：{e}")
        d += dt.timedelta(days=1)
    return rides, (None if failed else trains)


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


def hour_level(x, cfg):
    """逐小时分三档（本人 11-01 要求）：0 绿=适合，1 黄=勉强（小雨、风大、轻度污染），2 红=不适合（大雨、冷、热、大风、重污染）。
    返回 (档位, [原因])。黄、红都算"不适合"，只是红更严重。"""
    red, yellow = [], []
    if x["mm"] > cfg["heavy_rain_mm"] or x["pp"] >= cfg["heavy_rain_prob"]:
        red.append("大雨")
    elif x["pp"] > cfg["rain_prob_max"] or x["mm"] > cfg["rain_mm_max"]:
        yellow.append("小雨")
    if x["feel"] is not None and x["feel"] > cfg["feel_max"]:
        red.append("热")
    if x["feel"] is not None and x["feel"] < cfg["feel_min"]:
        red.append("冷")
    if x["wind"] > cfg["wind_red"] or x["gust"] > cfg["gust_red"]:
        red.append("大风")
    elif x["wind"] > cfg["wind_max"] or x["gust"] > cfg["gust_max"]:
        yellow.append("风大")
    if x["pm"] is not None and x["pm"] > cfg["pm25_red"]:
        red.append("雾霾")
    elif x["pm"] is not None and x["pm"] > cfg["pm25_max"]:
        yellow.append("轻度污染")
    return (2 if red else 1 if yellow else 0), red + yellow


def hour_problems(x, cfg):
    return hour_level(x, cfg)[1]


def judge_day(day, hours, cfg):
    """看这一天可骑时段里有没有足够长的连续好天气（只算绿档小时）。"""
    lo, hi = cfg.get("day_hours") or (cfg["weekend_hours"] if day.weekday() >= 5 else cfg["weekday_hours"])
    seg = [x for x in hours if x["t"].date() == day and lo <= x["t"].hour < hi]
    if not seg:
        return {"ok": False, "reason": "没有预报"}
    feel = [x["feel"] for x in seg if x["feel"] is not None]
    pms = [x["pm"] for x in seg if x["pm"] is not None]
    summary = (f"体感{min(feel):.0f}~{max(feel):.0f}° 降水概率最高{max(x['pp'] for x in seg)}% "
               f"风{max(x['wind'] for x in seg):.0f}km/h"
               + (f" PM2.5最高{max(pms):.0f}" if pms else ""))
    levels = [hour_level(x, cfg) for x in seg]
    probs = [p for _, p in levels]
    hour_bars = [{"h": x["t"].hour, "feel": x["feel"], "bad": p, "level": lv}
                 for x, (lv, p) in zip(seg, levels)]
    # 不适合的天也给弹窗画图用：全天指标 + 逐小时
    whole = {"summary": summary, "best": [0, 0],
             "feel": f"{min(feel):.0f}~{max(feel):.0f}°" if feel else "-",
             "wind": f"{max(x['wind'] for x in seg):.0f}", "pp": max(x["pp"] for x in seg),
             "pm": f"{max(pms):.0f}" if pms else None,
             "hours": hour_bars}
    start = dt.datetime.combine(day, dt.time(lo))
    rain12 = sum(x["mm"] for x in hours if start - dt.timedelta(hours=12) <= x["t"] < start)
    if rain12 > cfg["wet_road_mm"]:
        return {"ok": False, "reason": f"之前下雨{rain12:.0f}mm，路湿", **whole}
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
        return {"ok": False, "reason": "、".join(reasons) or "好天气时段太短", **whole}
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
            "hours": hour_bars}


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
    """每晚定时提醒（本人 10-31 要求）：不管明天天气好坏都弹，给出明天能不能骑的建议 + 本周已骑几次，
    好让周五周六也记得这周骑没骑、临时决定明天去不去。只有点了「这周不骑了」的周不弹。
    --preview：无视「这周不骑了」，弹一下看效果。"""
    preview = "--preview" in args
    cfg = load_config()
    today = dt.date.today()
    day = today + dt.timedelta(days=1)
    if not preview and week_start(day).isoformat() in load_skips():
        log("这周说了不骑，不提醒")
        return
    hours = fetch_forecast(cfg)
    j = judge_day(day, hours, cfg)
    rel = "明天"
    wd = f"周{WEEKDAY[day.weekday()]}"

    others = []  # 明天之后、本周之内还能骑的天
    d = day + dt.timedelta(days=1)
    while week_start(d) == week_start(day):
        jj = judge_day(d, hours, cfg)
        if jj["ok"]:
            others.append(f"周{WEEKDAY[d.weekday()]} {jj['window']}")
        d += dt.timedelta(days=1)

    # 本人 11-01：卡片分三块——今天做了什么（打卡）/ 明天建议 / 本周进度。
    # 本周按「今天所在的周」算（以前按明天算，周日晚上会把今天漏掉）；昨天有计划没记录的，也给补记的机会。
    yday = today - dt.timedelta(days=1)
    ws = week_start(today)
    xr, xt = xunji_week(today, today, start=min(ws, yday))
    ride_set = {dt.date.fromisoformat(r["date"]) for r in load_rides()} | xr
    manual_t = {dt.date.fromisoformat(r["date"]): (r.get("note") or "训练") for r in load_json(TRAINS, [])}
    train_map = dict(manual_t)
    if xt:
        train_map.update(xt)
    plans = load_json(PLANS, {})

    def wds(ds):
        return "、".join(f"周{WEEKDAY[x.weekday()]}" for x in ds)

    def wd_short(ds):
        return "、".join(WEEKDAY[x.weekday()] for x in ds)

    rode_today, trained_today = today in ride_set, train_map.get(today)
    # ① 今天
    today_lines = []
    if rode_today:
        today_lines.append("✅ 骑车了")
    if trained_today:
        today_lines.append(f"✅ 练了：{trained_today}")
    if not today_lines:
        today_lines.append("还没记录骑车或训练" + ("（训记暂时没读到）" if xt is None and os.path.exists(XUNJI_SKILL) else ""))
    tp = plans.get(today.isoformat())
    if tp == "ride" and not rode_today:
        today_lines.append("昨晚计划今天骑车，还没记录——骑了就点「今天骑了」")
    if tp == "train" and not trained_today:
        today_lines.append("昨晚计划今天练单杠，还没记录——练了就点「今天练了」")
    yp = plans.get(yday.isoformat())
    yday_plan = yp if (yp == "ride" and yday not in ride_set) or (yp == "train" and yday not in train_map) else None
    # ③ 本周（今天所在的周）
    week_rides = sorted(d for d in ride_set if ws <= d <= today)
    week_trains = sorted(d for d in train_map if ws <= d <= today)
    head = "本周小结" if today.weekday() == 6 else "本周"
    week = (f"{head}：" + (f"骑{len(week_rides)}次（{wd_short(week_rides)}）✅" if week_rides else "还没骑")
            + "  " + (f"练{len(week_trains)}次（{wd_short(week_trains)}）" if week_trains else "还没练单杠"))
    if today.weekday() == 6:
        week += "\n明天开始新的一周"
    # ② 明天建议（户外单杠对天气的要求和骑车差不多）
    rides_left = not week_rides or today.weekday() == 6  # 周日晚上：明天是新的一周，骑车次数清零
    if not j["ok"]:
        advice = "明天不太适合户外，骑车和单杠都先缓缓"
    elif trained_today:
        advice = f"今天练了{trained_today}，明天去骑车，单杠歇一天"
    elif rode_today:
        advice = "今天骑过了，明天适合练单杠"
    elif rides_left and day.weekday() == 6:
        advice = "明天是本周最后一天，抓住机会骑一趟"
    elif rides_left:
        advice = "明天骑车、练单杠都合适，优先骑车"
    else:
        advice = "本周骑车已达标，明天可以练单杠，或者再骑一趟"
    later = ("本周后面适合户外：" + "、".join(others)) if others else (
        "" if day.weekday() == 6 else "本周后面天气也都不太行")

    title = f"{rel}适合骑车·练单杠" if j["ok"] else f"{rel}不适合骑车·练单杠"
    tail = f"{week}\n{advice}" + (f"\n{later}" if later else "")
    body = (f"今天：{'；'.join(today_lines)}\n\n{cfg['city']} {rel} {wd}："
            + (f"{j['window']} {j['detail']}" if j["ok"] else j.get("reason", "")) + f"\n\n{tail}")
    popup({
        "title": title, "body": body, "ok": j["ok"], "rel": rel, "reason": j.get("reason", ""),
        "sub": f"{cfg['city']} · {day.month}月{day.day}日 {wd}",
        "window": j.get("window", ""), "feel": j.get("feel", "-"), "wind": j.get("wind", "-"),
        "pp": j.get("pp", "-"), "pm": j.get("pm"), "hours": j.get("hours", []), "best": j.get("best", [0, 0]),
        "day": day.isoformat(), "warn": j.get("warn"), "today": today.isoformat(), "yday": yday.isoformat(),
        "today_title": f"今天 {today.month}月{today.day}日 周{WEEKDAY[today.weekday()]}",
        "today_lines": today_lines, "rode_today": rode_today, "trained_today": bool(trained_today),
        "yday_plan": yday_plan, "advice": advice, "week": week, "later": later,
        # 右下角推荐哪个：今天骑了没练 → 明天去练；其余 → 明天去骑
        "suggest": "train" if (rode_today and not trained_today) or (not rides_left and not trained_today) else "ride",
        "tail": tail,
    })
    if not preview:
        log(f"已提醒：{title} {j.get('window') or j.get('reason', '')}；{status}")


def _date_note(args):
    has_date = bool(args) and args[0][:2] == "20"
    d = dt.date.fromisoformat(args[0]) if has_date else dt.date.today()
    return d, " ".join(args[1:] if has_date else args)


def cmd_log(args):
    d, note = _date_note(args)
    add_record(RIDES, d, note)
    print(f"已记录骑行 {d} 周{WEEKDAY[d.weekday()]} {note}")


def cmd_train(args):
    """训记里没记的训练手动补一条：train [日期] [内容]"""
    d, note = _date_note(args)
    add_record(TRAINS, d, note or "训练")
    print(f"已记录训练 {d} 周{WEEKDAY[d.weekday()]} {note}")


def cmd_plan(args):
    """记下明天（或指定日期）要去骑车/练单杠：plan [日期] [ride|train]"""
    what = args.pop() if args and args[-1] in ("ride", "train") else "ride"
    d = dt.date.fromisoformat(args[0]) if args else dt.date.today() + dt.timedelta(days=1)
    set_plan(d, what)
    print(f"已记下计划：{d} 周{WEEKDAY[d.weekday()]} {'骑车' if what == 'ride' else '练单杠'}")


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
            "log": cmd_log, "train": cmd_train, "plan": cmd_plan, "status": cmd_status, "skip": cmd_skip}
    if a[0] not in cmds:
        sys.exit(__doc__)
    try:
        cmds[a[0]](a[1:])
    except Exception as e:
        log(f"出错：{e!r}")
        raise
