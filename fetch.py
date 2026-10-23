"""步驟 2：每日抓資料，原始資料存到 data/raw/<日期>/。

- 櫃買中心 OpenAPI：可轉債發行條件、上櫃股價
- 櫃買中心每日行情 CSV：可轉債收盤價
- 證交所每日收盤行情：上市股價
- 證交所＋櫃買中心：三大法人買賣超日報（一次全市場，近 5 個交易日）

用法：
  python fetch.py                    # 今天
  python fetch.py 2026-10-20         # 指定日期
  python fetch.py 2026-10-20 --no-inst   # 跳過法人
"""
import csv
import datetime as dt
import io
import json
import sys
import time

import requests

import config as C

HEADERS = {"User-Agent": "ntpu-gdg-cb-project"}


def get_json(url):
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def save(day, name, data):
    folder = C.RAW_DIR / day
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {name}: {len(data)} 筆")


def parse_cb_csv(text):
    """解析櫃買中心可轉債行情 CSV。

    每行第一欄是標籤，只取 BODY；每檔有「等價」「議價」兩列，
    議價列沒有代號，直接略過，只保留等價列。
    """
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or rec[0].strip() != "BODY":
            continue
        f = [x.strip() for x in rec[1:]]
        # 欄位：代號,名稱,交易,收市,漲跌,開市,最高,最低,筆數,單位,金額,均價,明日參價,明日漲停,明日跌停
        if len(f) < 12 or not f[0]:
            continue
        rows.append({
            "cb_id": f[0], "cb_name": f[1], "close": f[3], "change": f[4],
            "volume": f[9].replace(",", ""), "avg": f[11],
        })
    return rows


def fetch_cb_quotes(day):
    d = dt.date.fromisoformat(day)
    url = C.CB_QUOTES_URL.format(y=d.strftime("%Y"), ym=d.strftime("%Y%m"), ymd=d.strftime("%Y%m%d"))
    r = requests.get(url, timeout=30, headers=HEADERS)
    if r.status_code == 404:
        raise SystemExit(f"{day} 沒有可轉債行情檔（非交易日，或收盤後檔案還沒產生）。換一個日期試試。")
    r.raise_for_status()
    return parse_cb_csv(r.content.decode("cp950", errors="replace"))


def fetch_twse_stocks(day):
    """上市股價：用證交所「每日收盤行情」，可以指定日期。

    OpenAPI 的 STOCK_DAY_ALL 只給最新一天，而且更新得比櫃買中心晚，
    晚上抓還可能拿到前一天的股價，所以改用這個。
    """
    ymd = day.replace("-", "")
    j = get_json(C.TWSE_DAILY_URL.format(ymd=ymd))
    if j.get("stat") != "OK":
        raise SystemExit(f"證交所 {day} 沒有資料：{j.get('stat')}（非交易日，或資料還沒產生）")
    # 回傳裡有好幾張表，找出有「證券代號」和「收盤價」的那張
    tables = j.get("tables") or [{"fields": j[k], "data": j.get("data" + k[6:], [])}
                                 for k in j if k.startswith("fields")]
    for t in tables:
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            i_code, i_close = f.index("證券代號"), f.index("收盤價")
            return [{"Code": r[i_code].strip(), "ClosingPrice": r[i_close], "Date": ymd}
                    for r in t.get("data", [])]
    raise SystemExit("證交所回傳格式改了，找不到「證券代號、收盤價」欄位，請把錯誤訊息貼給 Claude。")


def fetch_open_data(day):
    save(day, "cb_terms", get_json(C.TPEX_BASE + C.CB_TERMS_PATH))
    save(day, "cb_quotes", fetch_cb_quotes(day))
    save(day, "stock_twse", fetch_twse_stocks(day))
    save(day, "stock_tpex", get_json(C.TPEX_BASE + C.TPEX_STOCK_PATH))


def _num(x):
    try:
        return int(str(x).replace(",", "").strip())
    except ValueError:
        return None


def inst_one_day(d):
    """抓某一天上市＋上櫃的三大法人買賣超（股），回傳 [{date, stock_id, net}]；非交易日回傳空清單。"""
    ymd = d.strftime("%Y%m%d")
    rows = []
    # 上市：證交所 T86
    j = get_json(C.TWSE_INST_URL.format(ymd=ymd))
    if j.get("stat") == "OK":
        f = j["fields"]
        i_code, i_net = f.index("證券代號"), f.index("三大法人買賣超股數")
        rows += [{"date": d.isoformat(), "stock_id": r[i_code].strip(), "net": _num(r[i_net])}
                 for r in j.get("data", [])]
    time.sleep(3)  # 證交所、櫃買中心對連續請求比較敏感，放慢一點
    # 上櫃：櫃買中心三大法人買賣明細
    j = get_json(C.TPEX_INST_URL.format(ymd=d.strftime("%Y/%m/%d")))
    for t in j.get("tables", []):
        f = t.get("fields") or []
        if "代號" in f and t.get("data"):
            i_net = next(i for i, x in enumerate(f) if x.startswith("三大法人買賣超"))
            rows += [{"date": d.isoformat(), "stock_id": str(r[0]).strip(), "net": _num(r[i_net])}
                     for r in t["data"]]
    return rows


def fetch_institutional(day):
    """官方三大法人日報：一次拿全市場，往前找最近 5 個交易日。

    抓過的日期會存在 data/raw/<那天>/inst_day.json，下次直接讀，不重抓。
    """
    d = dt.date.fromisoformat(day)
    rows, days_found, tries = [], 0, 0
    while days_found < C.INST_DAYS and tries < 12:
        cache = C.RAW_DIR / d.isoformat() / "inst_day.json"
        if cache.exists():
            day_rows = json.loads(cache.read_text(encoding="utf-8"))
        elif d.weekday() >= 5:  # 週末不用查
            day_rows = []
        else:
            day_rows = inst_one_day(d)
            if day_rows:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(day_rows, ensure_ascii=False), encoding="utf-8")
            time.sleep(3)
        if day_rows:
            rows += day_rows
            days_found += 1
            print(f"  法人 {d}：{len(day_rows)} 檔")
        d -= dt.timedelta(days=1)
        tries += 1
    if days_found == 0:
        raise SystemExit(f"{day} 前後都抓不到三大法人資料，可能是網站格式改了，請截圖給 Claude。")
    save(day, "institutional", rows)


def write_meta(day):
    """記錄這批原始資料是什麼時候抓的，給 build.py 的 fetched_at 欄位用（資料來源/時戳追溯）。"""
    meta = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    (C.RAW_DIR / day / "_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else dt.date.today().isoformat()
    print(f"抓取 {day}")
    fetch_open_data(day)
    if "--no-inst" in sys.argv:
        print("已跳過法人資料。")
        write_meta(day)
        return
    fetch_institutional(day)
    write_meta(day)

if __name__ == "__main__":
    main()