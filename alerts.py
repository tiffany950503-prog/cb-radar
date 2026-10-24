"""步驟 4：讀當天的結果表，套用觸發條件，組成一則 LINE 摘要訊息。

用法：
  python alerts.py                # 用最新一天的結果表，只印出訊息、不發送
  python alerts.py 2026-10-23     # 指定日期
  python alerts.py --send         # 印出並透過 LINE 廣播給官方帳號的所有好友

發送需要環境變數 LINE_CHANNEL_TOKEN（LINE Developers 的 channel access token）。
訊息也會存成 data/alerts_<日期>.txt，方便回頭查。
"""
import datetime as dt
import os
import sys

import pandas as pd
import requests

import config as C

# ── 觸發條件（2026-10-23 確認） ─────────────────────────────
PREMIUM_MAX = 0.0          # 溢價率低於這個值（0 = 折價）
MIN_VOLUME = 10            # 當天成交至少幾張
INST_NET_MIN = 500         # 三大法人近 5 日合計買超超過幾張
PUT_WITHIN_DAYS = 30       # 距離賣回日幾天內
MAX_ITEMS = 8              # 每一類最多列幾檔，避免訊息太長

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_TEXT_LIMIT = 5000     # LINE 單則文字訊息的字數上限


def load_day(day=None):
    if day:
        path = C.DATA_DIR / f"daily_{day}.csv"
    else:
        files = sorted(C.DATA_DIR.glob("daily_*.csv"))
        if not files:
            raise SystemExit("找不到 daily_*.csv，請先執行 fetch.py 和 build.py。")
        path = files[-1]
    if not path.exists():
        raise SystemExit(f"找不到 {path.name}，請先執行 build.py {day}。")
    df = pd.read_csv(path, dtype={"cb_id": str, "stock_id": str}, encoding="utf-8-sig")
    return path.stem.replace("daily_", ""), df


def to_date(x):
    """20271209、20271209.0、2027/12/09、2027-12-09 都轉成日期。"""
    s = str(x).strip().split(".")[0].replace("/", "").replace("-", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def flag(df, col):
    return df[col].astype(str).str.lower().isin(["true", "1"]) if col in df else pd.Series(False, index=df.index)


def find_alerts(df, day):
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0)
    premium = pd.to_numeric(df["premium"], errors="coerce")
    tradable = flag(df, "traded_today") & ~flag(df, "conv_not_started") & ~flag(df, "is_exchangeable")

    disc = df[tradable & (premium < PREMIUM_MAX) & (vol >= MIN_VOLUME)].copy()
    disc["premium"] = premium[disc.index]
    disc["volume"] = vol[disc.index]
    disc = disc.sort_values("premium")

    if "inst_net_5d" in df:
        inst_net = pd.to_numeric(df["inst_net_5d"], errors="coerce")
        inst = df[inst_net > INST_NET_MIN].copy()
        inst["inst_net_5d"] = inst_net[inst.index]
        inst = inst.sort_values("inst_net_5d", ascending=False)
    else:
        inst = df.iloc[0:0]

    today = dt.date.fromisoformat(day)
    days_left = df["put_date"].map(lambda x: (d - today).days if (d := to_date(x)) else None) \
        if "put_date" in df else pd.Series(None, index=df.index)
    days_left = pd.to_numeric(days_left, errors="coerce")
    put = df[(days_left >= 0) & (days_left <= PUT_WITHIN_DAYS)].copy()
    put["days_left"] = days_left[put.index]
    put = put.sort_values("days_left")
    return disc, inst, put


def previous_ids(day):
    """前一個交易日已經提醒過哪些（類別, 代號），用來標示「新」。"""
    files = sorted(f for f in C.DATA_DIR.glob("daily_*.csv") if f.stem.replace("daily_", "") < day)
    if not files:
        return set()
    prev_day, prev = load_day(files[-1].stem.replace("daily_", ""))
    d, i, p = find_alerts(prev, prev_day)
    return {("disc", x) for x in d["cb_id"]} | {("inst", x) for x in i["cb_id"]} | {("put", x) for x in p["cb_id"]}


def name(r):
    return f"{str(r['cb_name']).strip()} {r['cb_id']}"


def section(title, rows, kind, seen, fmt):
    if rows.empty:
        return []
    lines = [f"【{title}】{len(rows)} 檔"]
    for _, r in rows.head(MAX_ITEMS).iterrows():
        new = "（新）" if (kind, r["cb_id"]) not in seen else ""
        lines.append(f"・{name(r)}{new}：{fmt(r)}")
    if len(rows) > MAX_ITEMS:
        lines.append(f"・…另外還有 {len(rows) - MAX_ITEMS} 檔")
    return lines + [""]


def build_message(day, df):
    disc, inst, put = find_alerts(df, day)
    seen = previous_ids(day)
    d = dt.date.fromisoformat(day)
    lines = [f"可轉債雷達｜{d.month}/{d.day} 盤後", ""]
    lines += section("折價", disc, "disc", seen,
                     lambda r: f"溢價率 {r['premium']:+.1%}，成交 {int(r['volume'])} 張")
    lines += section("法人買超", inst, "inst", seen,
                     lambda r: f"近 5 日買超 {r['inst_net_5d']:,.0f} 張")
    lines += section("近賣回日", put, "put", seen,
                     lambda r: f"剩 {int(r['days_left'])} 天"
                               + (f"，賣回價 {float(r['put_price']):g}" if pd.notna(r.get("put_price")) else ""))
    if disc.empty and inst.empty and put.empty:
        lines += ["今天沒有符合條件的可轉債。", ""]
    lines += [f"條件：溢價率 < {PREMIUM_MAX:.0%}、成交 ≥ {MIN_VOLUME} 張；法人 5 日買超 > {INST_NET_MIN} 張；"
              f"賣回日 {PUT_WITHIN_DAYS} 天內",
              "僅供研究，不構成投資建議。"]
    text = "\n".join(lines)
    if len(text) > LINE_TEXT_LIMIT:
        text = text[:LINE_TEXT_LIMIT - 20] + "\n…（訊息過長已截斷）"
    return text, (len(disc), len(inst), len(put))


def send_line(text):
    token = os.getenv("LINE_CHANNEL_TOKEN")
    if not token:
        raise SystemExit("沒有設定 LINE_CHANNEL_TOKEN，無法發送。請先在 LINE Developers 取得 channel access token。")
    r = requests.post(LINE_BROADCAST_URL, timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      json={"messages": [{"type": "text", "text": text}]})
    if r.status_code != 200:
        raise SystemExit(f"LINE 發送失敗（HTTP {r.status_code}）：{r.text[:200]}")
    print("✓ 已透過 LINE 廣播發送。")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day, df = load_day(args[0] if args else None)
    text, counts = build_message(day, df)
    print(text)
    print(f"\n（折價 {counts[0]} 檔、法人買超 {counts[1]} 檔、近賣回日 {counts[2]} 檔，共 {len(text)} 字）")
    (C.DATA_DIR / f"alerts_{day}.txt").write_text(text, encoding="utf-8")
    if "--send" in sys.argv:
        send_line(text)
    else:
        print("只預覽、沒有發送。要發送請加 --send。")


if __name__ == "__main__":
    main()