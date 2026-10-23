"""步驟 3：把四種原始資料串成一張表，算溢價率，存進 SQLite 並輸出 CSV。

用法：python build.py            # 處理今天
      python build.py 2026-10-21 # 處理指定日期
"""
import datetime as dt
import json
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

import config as C

WEB_DIR = C.DATA_DIR.parent / "docs" / "data"
HISTORY_DAYS = 60  # 網頁歷史圖保留的交易日數
LATEST_FIELDS = ["cb_id", "cb_name", "stock_id", "cb_close", "stock_close", "conv_price_now",
                 "premium", "premium_null_reason", "signal", "inst_net_5d", "volume",
                 "traded_today", "conv_not_started", "is_exchangeable", "date",
                 "conv_price_asof", "fetched_at"]
HISTORY_FIELDS = ["cb_id", "cb_name", "date", "premium", "traded_today"]

# 健康檢查／訊號分級的警戒線（依 2026-10-21 正常資料設定：389 檔、99.5% 有股價、44% 有成交）
MIN_CB_COUNT = 300        # 可轉債總數低於這個數，可能是發行資料沒抓完整
MIN_STOCK_COVERAGE = 0.95  # 有股價的比例
MIN_TRADED_RATIO = 0.15    # 有成交的比例；太低通常是抓到非交易日或行情檔不完整
SUSPICIOUS_DISCOUNT = -0.05  # 折價超過 5% 很少見，多半是轉換價沒更新
EXTREME_PREMIUM = 5.0        # 溢價率超過 500%，可轉債幾乎只剩債券價值
MIN_VOLUME = 10              # 成交量（張）低於這個數，量太薄不當訊號；粗略估計值，可調


def load(day, name):
    p = C.RAW_DIR / day / f"{name}.json"
    return pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else pd.DataFrame()


def fetched_at(day):
    """讀 fetch.py 寫的 _meta.json，回傳這批原始資料的抓取時間；沒有就回傳 None
    （例如舊資料、或手動補的歷史資料沒有這個檔案）。"""
    p = C.RAW_DIR / day / "_meta.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("fetched_at")


def pick(df, fields):
    """依 config 的欄位對照表取欄位並改名；缺欄位時直接報錯，提醒去改對照表。"""
    missing = [v for v in fields.values() if v not in df.columns]
    if missing:
        raise KeyError(f"找不到欄位 {missing}，實際欄位是 {list(df.columns)}，請改 config.py")
    return df[list(fields.values())].rename(columns={v: k for k, v in fields.items()})


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def select_known_adjustments(adj, day):
    """只留『當時已經知道』的轉換價調整：生效日 <= day，而且是我們在 day（含）之前抓到的。

    `recorded_at` 缺值（舊資料沒記錄抓取時間）時，保守假設跟 effective_date 同時已知。
    抽成獨立函式方便單獨測試，不用碰檔案系統。
    """
    if adj.empty:
        return adj
    recorded_at = adj["recorded_at"].fillna(adj["effective_date"]) if "recorded_at" in adj else adj["effective_date"]
    known = adj[(adj["effective_date"] <= day) & (recorded_at <= day)]
    return known.sort_values("effective_date").groupby("cb_id").tail(1)


def adjusted_prices(day):
    """轉換價調整表：從 MOPS 公告整理，格式 cb_id, conv_price, effective_date[, recorded_at]。
    MOPS 爬蟲沿用既有監控工具，輸出成 data/conv_price_adjustments.csv 即可。

    只套用「day 當時已經知道」的調整（見 select_known_adjustments），避免之後重新執行
    build.py 處理過去日期時，用「現在才知道的回溯調整」悄悄覆蓋「那天當時看到的轉換價」
    （look-ahead bias）。回傳值多一個 asof：這次實際套用的調整裡最新的生效日，隨 build()
    結果一起存起來，方便回頭追查每一筆歷史 premium 是依據哪個版本算出來的。
    """
    p = C.DATA_DIR / "conv_price_adjustments.csv"
    if not p.exists():
        return pd.DataFrame(columns=["cb_id", "conv_price_adj"]), None
    # utf-8-sig 會去掉 Excel／PowerShell 存檔時加在開頭的隱形標記；欄位名稱也去掉前後空白
    adj = pd.read_csv(p, dtype=str, encoding="utf-8-sig", skipinitialspace=True)
    adj.columns = adj.columns.str.strip().str.lstrip("﻿")
    need = {"cb_id", "conv_price", "effective_date"}
    if not need <= set(adj.columns):
        raise KeyError(f"conv_price_adjustments.csv 第一行要是 cb_id,conv_price,effective_date，"
                       f"目前讀到的是 {list(adj.columns)}")
    adj["cb_id"] = adj["cb_id"].str.strip()
    known = select_known_adjustments(adj, day)
    asof = known["effective_date"].max() if len(known) else None
    latest = known.rename(columns={"conv_price": "conv_price_adj"})[["cb_id", "conv_price_adj"]]
    return latest, asof


def _dec(x):
    """字串轉 Decimal，避免 float 轉換再引入新的二進位誤差；缺值回傳 None。"""
    return None if pd.isna(x) else Decimal(str(x))


def _valuation_decimal(row):
    """單列算轉換價值、溢價率，核心除法用 Decimal 避免浮點誤差，算完量化再轉回 float。

    上游資料從 CSV/JSON 字串轉 float（to_num）這一步本來就已經過一次二進位轉換；
    這裡只處理「核心除法」這一段的精度，不是從頭到尾的字串-to-Decimal 全鏈路——
    對 400 多檔、台股最小跳動單位的場景，再往上游改沒有實際差異，風險／效益不成比例。
    """
    conv_price_now, stock_close, cb_close = (_dec(row["conv_price_now"]), _dec(row["stock_close"]),
                                               _dec(row["cb_close"]))
    if conv_price_now is None or stock_close is None or cb_close is None or conv_price_now <= 0:
        return float("nan"), float("nan")
    conv_value = (Decimal(100) / conv_price_now * stock_close).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    premium = (cb_close / conv_value - 1).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return float(conv_value), float(premium)


def calc_valuation(df):
    """核心公式：轉換價值、溢價率，以及 null 理由代碼與訊號分級。

    輸入 df 至少要有 cb_close, stock_close, conv_price_now, conv_start, cb_name, date, volume 欄位。
    回傳加上 conv_value, premium, premium_null_reason, is_exchangeable, conv_not_started,
    traded_today, signal 的 df。抽成獨立函式方便單獨測試，不用跑整條 fetch+merge 流程。
    """
    t = df.copy()
    t[["conv_value", "premium"]] = t.apply(_valuation_decimal, axis=1, result_type="expand")

    t["is_exchangeable"] = t["cb_name"].astype(str).str.contains("E")  # 交換債，換的是別家股票
    t["conv_not_started"] = pd.to_numeric(t["conv_start"], errors="coerce") > t["date"].str.replace("-", "").astype(int)
    t["traded_today"] = t["cb_close"].notna()

    # null 理由代碼：premium 為空時一定要能回答「為什麼」，不能只是裸的 NaN（規劃書要求，也不能回傳 0）
    no_conv_price = t["conv_price_now"].isna() | (t["conv_price_now"] <= 0)
    # pd.Series(None, dtype=object) 會悄悄把 None 變成 NaN；用 np.full 才能保留真正的 None
    reason = pd.Series(np.full(len(t), None, dtype=object), index=t.index)
    reason[t["is_exchangeable"]] = "exchangeable"
    reason[~t["traded_today"] & reason.isna()] = "not_traded"
    reason[t["stock_close"].isna() & reason.isna()] = "no_stock_price"
    reason[no_conv_price & reason.isna()] = "no_conv_price"
    t["premium_null_reason"] = reason
    t.loc[reason.notna(), "premium"] = float("nan")

    # 訊號分級：跟「這數字能不能直接拿來判斷套利」分開判斷，複用健康檢查的門檻＋成交量門檻
    # （成交量太薄，就算折價也不是能執行的訊號，只是沒人要賣）
    enough_volume = pd.to_numeric(t["volume"], errors="coerce").fillna(0) >= MIN_VOLUME
    tradable = (t["traded_today"] & ~t["conv_not_started"] & ~t["is_exchangeable"]
                & t["premium"].notna() & enough_volume)
    t["signal"] = "none"
    t.loc[tradable & (t["premium"] < 0) & (t["premium"] >= SUSPICIOUS_DISCOUNT), "signal"] = "candidate"
    t.loc[tradable & (t["premium"] < SUSPICIOUS_DISCOUNT), "signal"] = "suspect"
    return t


def roc(day):
    """2026-10-20 → 1151020（櫃買中心 OpenAPI 用民國日期）"""
    d = dt.date.fromisoformat(day)
    return f"{d.year - 1911}{d:%m%d}"


def check_stock_date(day, raw, label):
    """OpenAPI 只給「最新一天」的股價。如果和可轉債行情日期不同，溢價率會用錯天的股價。"""
    if "Date" in raw.columns and len(raw):
        got = str(raw["Date"].iloc[0]).strip()
        if got not in (roc(day), day.replace("-", "")):
            print(f"⚠ {label} 股價日期是 {got}，不是 {roc(day)}。"
                  f"請改用最新交易日重新執行 fetch.py 和 build.py，否則溢價率會用到不同天的股價。")


def build(day):
    raw_twse, raw_tpex = load(day, "stock_twse"), load(day, "stock_tpex")
    check_stock_date(day, raw_twse, "上市")
    check_stock_date(day, raw_tpex, "上櫃")
    terms = pick(load(day, "cb_terms"), C.TERMS_FIELDS)
    # 私募債沒有在市場交易、代號不是 5 碼數字，先排除
    terms["cb_id"] = terms["cb_id"].astype(str).str.strip()
    terms = terms[terms["cb_id"].str.fullmatch(r"\d{5,6}")].copy()
    quotes = pick(load(day, "cb_quotes"), C.QUOTE_FIELDS).rename(columns={"close": "cb_close"})
    stocks = pd.concat([
        pick(raw_twse, C.TWSE_STOCK_FIELDS),
        pick(raw_tpex, C.TPEX_STOCK_FIELDS),
    ]).rename(columns={"close": "stock_close"})

    for df, col in [(terms, "cb_id"), (quotes, "cb_id"), (stocks, "stock_id")]:
        df[col] = df[col].astype(str).str.strip()
    terms["stock_id"] = terms["cb_id"].str[:4]

    adj, conv_price_asof = adjusted_prices(day)
    t = (terms.merge(quotes, on="cb_id", how="left")
              .merge(stocks, on="stock_id", how="left")
              .merge(adj, on="cb_id", how="left"))

    for col in ["conv_price", "conv_price_adj", "cb_close", "stock_close", "put_price", "volume"]:
        if col in t:
            t[col] = to_num(t[col])
    # 有調整過就用最新轉換價，否則用發行時轉換價
    t["conv_price_now"] = t["conv_price_adj"].fillna(t["conv_price"])
    t["date"] = day
    t["conv_price_asof"] = conv_price_asof  # 這批 premium 是用哪個版本的轉換價調整表算出來的
    t["fetched_at"] = fetched_at(day)  # 這批原始資料是哪次 fetch.py 跑出來的

    t = calc_valuation(t)

    # 三大法人：每檔股票最近 5 個交易日的合計買賣超（股數 → 張）
    inst = load(day, "institutional")
    if not inst.empty:
        inst["stock_id"] = inst["stock_id"].astype(str).str.strip()
        if "net" in inst:  # 官方日報：已經是三大法人合計買賣超（股）
            inst["net"] = to_num(inst["net"]) / 1000
        else:              # 舊的 FinMind 格式：每個法人一列，有買進、賣出
            inst["net"] = (to_num(inst["buy"]) - to_num(inst["sell"])) / 1000
        daily = inst.groupby(["stock_id", "date"])["net"].sum().reset_index().sort_values("date")
        last5 = daily.groupby("stock_id").tail(5)
        t = t.merge(last5.groupby("stock_id")["net"].sum().rename("inst_net_5d").reset_index(),
                    on="stock_id", how="left")

    out = t.sort_values("premium")
    C.DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(C.DB_PATH) as con:
        old = pd.read_sql("SELECT * FROM daily", con) if _has_table(con) else pd.DataFrame()
        if not old.empty:
            old = old[old["date"] != day]  # 同一天重跑時換掉舊資料，避免重複
        # 欄位有增減時，舊資料一起重寫，資料庫才不會因為欄位不同而存不進去
        pd.concat([old, out], ignore_index=True).to_sql("daily", con, if_exists="replace", index=False)
    out.to_csv(C.DATA_DIR / f"daily_{day}.csv", index=False, encoding="utf-8-sig")
    disc = out[(out["premium"] < 0) & ~out["conv_not_started"]]
    print(f"{day}：{len(out)} 檔上市櫃可轉債，{out['traded_today'].sum()} 檔今天有成交，"
          f"{len(disc)} 檔折價且已可轉換")
    meta = health_check(out)
    export_web(out, meta)
    return out


def health_check(out):
    """印出每日資料健康狀況，並回傳摘要給 export_web 用。⚠ 代表數字不正常，要回頭檢查資料來源。"""
    n = len(out)
    stock_cov = out["stock_close"].notna().mean() if n else 0
    traded = out["traded_today"].mean() if n else 0
    no_conv = out["conv_price_now"].isna() | (out["conv_price_now"] <= 0)
    tradable = out[out["traded_today"] & ~out["conv_not_started"]]
    deep = tradable[tradable["premium"] < SUSPICIOUS_DISCOUNT]
    extreme = (out["premium"] > EXTREME_PREMIUM).sum()

    def line(ok, text):
        print(f"  {'✓' if ok else '⚠'} {text}")

    print("健康檢查：")
    line(n >= MIN_CB_COUNT, f"可轉債總數 {n} 檔（正常 ≥ {MIN_CB_COUNT}）")
    line(stock_cov >= MIN_STOCK_COVERAGE, f"有股價 {stock_cov:.1%}（正常 ≥ {MIN_STOCK_COVERAGE:.0%}）")
    line(traded >= MIN_TRADED_RATIO, f"有成交 {traded:.1%}（正常 ≥ {MIN_TRADED_RATIO:.0%}）")
    line(not no_conv.any(), f"缺轉換價 {no_conv.sum()} 檔")
    line(deep.empty, f"折價超過 {-SUSPICIOUS_DISCOUNT:.0%} 的 {len(deep)} 檔（請確認轉換價是否已調整）")
    print(f"  · 溢價率超過 {EXTREME_PREMIUM:.0%} 的 {extreme} 檔（債性高，不是錯誤）")

    missing_stock = out[out["stock_close"].isna()]
    if len(missing_stock):
        names = "、".join(missing_stock["cb_name"].astype(str).str.strip().head(5))
        print(f"  · 沒有股價：{names}{' 等' if len(missing_stock) > 5 else ''}（可能是興櫃或暫停交易）")
    for _, r in deep.iterrows():
        print(f"    - {r['cb_id']} {str(r['cb_name']).strip()}：溢價率 {r['premium']:.2%}，轉換價 {r['conv_price_now']}")

    warnings = []
    if n < MIN_CB_COUNT:
        warnings.append(f"可轉債總數偏低（{n} 檔）")
    if stock_cov < MIN_STOCK_COVERAGE:
        warnings.append(f"股價覆蓋率偏低（{stock_cov:.1%}）")
    if traded < MIN_TRADED_RATIO:
        warnings.append(f"成交率偏低（{traded:.1%}）")
    if no_conv.any():
        warnings.append(f"{no_conv.sum()} 檔缺轉換價")
    if not deep.empty:
        warnings.append(f"{len(deep)} 檔折價超過 {-SUSPICIOUS_DISCOUNT:.0%}，請確認轉換價")
    return {"n": n, "stock_coverage": round(float(stock_cov), 4),
            "traded_ratio": round(float(traded), 4), "warnings": warnings}


def export_web(out, meta):
    """把當天排行榜與近期歷史寫成給 docs/ 前端讀的精簡 JSON。"""
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    day = out["date"].iloc[0] if len(out) else dt.date.today().isoformat()

    latest = out[LATEST_FIELDS].copy()
    for col in ["is_exchangeable", "conv_not_started", "traded_today"]:
        latest[col] = latest[col].astype(bool)
    # json 模組的 NaN 不是合法 JSON（瀏覽器 JSON.parse 會整個失敗），缺值一律改存 null
    latest = latest.astype(object).where(pd.notna(latest), None)
    payload = {"meta": {"date": day, **meta}, "rows": latest.to_dict(orient="records")}
    (WEB_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with sqlite3.connect(C.DB_PATH) as con:
        hist = pd.read_sql("SELECT cb_id, cb_name, date, premium, traded_today FROM daily", con)
    recent_days = sorted(hist["date"].unique())[-HISTORY_DAYS:]
    hist = hist[hist["date"].isin(recent_days)].copy()
    hist["traded_today"] = hist["traded_today"].astype(bool)
    hist = hist[HISTORY_FIELDS].astype(object).where(pd.notna(hist[HISTORY_FIELDS]), None)
    (WEB_DIR / "history.json").write_text(
        json.dumps(hist.to_dict(orient="records"), ensure_ascii=False), encoding="utf-8")
    print(f"  · 已輸出網頁資料：docs/data/latest.json、docs/data/history.json（近 {len(recent_days)} 個交易日）")


def _has_table(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='daily'").fetchone() is not None


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat())