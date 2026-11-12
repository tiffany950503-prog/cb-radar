"""步驟 3：把四種原始資料串成一張表，算溢價率，存進 SQLite 並輸出 CSV。

用法：python build.py            # 處理今天
      python build.py 2026-10-21 # 處理指定日期
"""
import datetime as dt
import gzip
import json
import sqlite3
import sys

import pandas as pd

import config as C


def load(day, name):
    p = C.RAW_DIR / day / f"{name}.json"
    return pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else pd.DataFrame()


def pick(df, fields):
    """依 config 的欄位對照表取欄位並改名；缺欄位時直接報錯，提醒去改對照表。"""
    missing = [v for v in fields.values() if v not in df.columns]
    if missing:
        raise KeyError(f"找不到欄位 {missing}，實際欄位是 {list(df.columns)}，請改 config.py")
    return df[list(fields.values())].rename(columns={v: k for k, v in fields.items()})


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def norm_date(x):
    """20290911、20290911.0、2029/09/11、2029-09-11、民國 1180911 → 2029-09-11；看不懂回傳 None。"""
    s = str(x).strip().split(".")[0].replace("/", "").replace("-", "")
    return to_iso(s)


def adjusted_prices(day):
    """轉換價調整表 data/conv_price_adjustments.csv（cb_id, conv_price, effective_date）。

    只採用「生效日 ≤ 當天」的紀錄，每檔取最新一筆。這樣未來才生效的轉換價不會提早套用，
    重算過去的日子時也會用到當時正確的轉換價。"""
    p = C.DATA_DIR / "conv_price_adjustments.csv"
    if not p.exists():
        return pd.DataFrame(columns=["cb_id", "conv_price_adj"])
    # utf-8-sig 會去掉 Excel／PowerShell 存檔時加在開頭的隱形標記；欄位名稱也去掉前後空白
    adj = pd.read_csv(p, dtype=str, encoding="utf-8-sig", skipinitialspace=True)
    adj.columns = adj.columns.str.strip().str.lstrip("﻿")
    need = {"cb_id", "conv_price", "effective_date"}
    if not need <= set(adj.columns):
        raise KeyError(f"conv_price_adjustments.csv 第一行要是 cb_id,conv_price,effective_date，"
                       f"目前讀到的是 {list(adj.columns)}")
    adj["cb_id"] = adj["cb_id"].str.strip()
    adj["effective_date"] = adj["effective_date"].map(norm_date)
    future = adj["effective_date"].notna() & (adj["effective_date"] > day)
    if future.any():
        print(f"  {future.sum()} 筆轉換價還沒生效，先不套用：" + "、".join(adj.loc[future, "cb_id"].head(5)))
    adj = adj[adj["effective_date"].notna() & ~future].sort_values("effective_date")
    return (adj.groupby("cb_id").tail(1)
               .rename(columns={"conv_price": "conv_price_adj", "effective_date": "conv_price_effective"})
               [["cb_id", "conv_price_adj", "conv_price_effective"]])


def roc(day):
    """2026-10-20 → 1151020（櫃買中心 OpenAPI 用民國日期）"""
    d = dt.date.fromisoformat(day)
    return f"{d.year - 1911}{d:%m%d}"


def to_iso(d):
    """20261021 或民國 1151021 → 2026-10-21；看不懂就回傳 None。"""
    s = str(d).strip()
    if len(s) == 7 and s.isdigit():
        s = f"{int(s[:3]) + 1911}{s[3:]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


TWD_WORDS = {"TWD", "NTD", "NT$", "", "NAN", "NONE"}


def only_twd(terms):
    """只留新台幣計價的可轉債（文件 2.2：排除外幣債）。看不懂幣別格式時不篩選，只提醒。"""
    if "currency" not in terms:
        return terms, 0
    cur = terms["currency"].astype(str).str.strip()
    twd = cur.str.upper().isin(TWD_WORDS) | cur.str.contains("台幣|臺幣|新台|新臺")
    if not twd.any():
        print(f"⚠ 看不懂幣別欄位的格式（例如 {cur.iloc[0]!r}），這次沒有排除外幣債，請截圖給 Claude。")
        return terms, 0
    excluded = terms[~twd]
    if len(excluded):
        kinds = "、".join(sorted(set(cur[~twd]))[:5])
        print(f"  已排除外幣債 {len(excluded)} 檔（幣別：{kinds}）")
    return terms[twd].copy(), len(excluded)


def drop_expired(terms, day):
    """排除已到期、或餘額為 0（已全部轉換、買回）的可轉債。"""
    maturity = terms["maturity"].map(norm_date) if "maturity" in terms else None
    balance = to_num(terms["balance"]) if "balance" in terms else None
    gone = pd.Series(False, index=terms.index)
    if maturity is not None:
        gone |= maturity.notna() & (maturity < day)
    if balance is not None:
        gone |= balance.notna() & (balance <= 0)
    if gone.any():
        print(f"  已排除到期或餘額為 0 的 {gone.sum()} 檔")
    return terms[~gone].copy(), int(gone.sum())


def map_stock_id(terms):
    """股票代號用櫃買中心的「發行公司代號」；沒有才用可轉債代號前 4 碼。兩者不一致的列入警告。"""
    prefix = terms["cb_id"].str[:4]
    if "issuer_code" in terms:
        issuer = terms["issuer_code"].astype(str).str.strip().replace({"": None, "nan": None, "None": None})
        terms["stock_id"] = issuer.fillna(prefix)
    else:
        terms["stock_id"] = prefix
    mismatch = terms["stock_id"] != prefix
    terms["code_mismatch"] = mismatch
    if mismatch.any():
        pairs = "、".join(f"{c}→{s}" for c, s in zip(terms.loc[mismatch, "cb_id"], terms.loc[mismatch, "stock_id"]))
        print(f"  發行公司代號和可轉債代號前 4 碼不同的 {mismatch.sum()} 檔（多半是交換債）：{pairs[:120]}")
    return terms, int(mismatch.sum())


def check_stock_date(day, raw, label):
    """OpenAPI 只給「最新一天」的股價。如果和可轉債行情日期不同，溢價率會用錯天的股價。"""
    if "Date" in raw.columns and len(raw):
        got = str(raw["Date"].iloc[0]).strip()
        if got not in (roc(day), day.replace("-", "")):
            print(f"⚠ {label} 股價日期是 {got}，不是 {roc(day)}。"
                  f"請改用最新交易日重新執行 fetch.py 和 build.py，否則溢價率會用到不同天的股價。")


LOW_VOLUME = 10          # 當天成交少於幾張，收盤價參考性低
MOPS_STALE_DAYS = 10     # MOPS 轉換價超過幾天沒更新就警告
PRICE_JUMP = 0.20        # 可轉債價格單日變動超過多少列入提醒


def mops_cross_check(t):
    """把 MOPS 的「發行時轉換價」和櫃買中心的對照，不一致代表資料對應可能出錯。"""
    path = C.DATA_DIR / "mops_snapshot.csv"
    if not path.exists():
        t["issue_price_check"] = "無 MOPS 資料"
        return t, 0
    snap = pd.read_csv(path, dtype={"cb_id": str}, encoding="utf-8-sig")[["cb_id", "original"]]
    t = t.merge(snap.rename(columns={"original": "mops_issue_price"}), on="cb_id", how="left")
    diff = (t["mops_issue_price"] - t["conv_price"]).abs()
    t["issue_price_check"] = "無 MOPS 資料"
    t.loc[diff <= 0.01, "issue_price_check"] = "一致"
    t.loc[diff > 0.01, "issue_price_check"] = "不一致"
    bad = t[t["issue_price_check"] == "不一致"]
    for _, r in bad.head(5).iterrows():
        print(f"    - {r['cb_id']} {str(r['cb_name']).strip()}：櫃買中心 {r['conv_price']:g}，MOPS {r['mops_issue_price']:g}")
    return t, len(bad)


def mops_age(day):
    """MOPS 轉換價距離上次更新幾天；沒有紀錄回傳 None。"""
    path = C.DATA_DIR / "mops_last_run.txt"
    if not path.exists():
        return None
    try:
        last = dt.date.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return (dt.date.fromisoformat(day) - last).days


def compare_previous(out, day):
    """跟前一個交易日比：檔數變化、沒有 MOPS 紀錄卻變動的轉換價、價格單日大幅變動。"""
    files = sorted(f for f in C.DATA_DIR.glob("daily_*.csv") if f.stem.replace("daily_", "") < day)
    if not files:
        return {"prev_day": None, "count_change": 0.0, "unexplained": pd.DataFrame(), "jumps": pd.DataFrame()}
    prev_day = files[-1].stem.replace("daily_", "")
    prev = pd.read_csv(files[-1], dtype={"cb_id": str}, encoding="utf-8-sig")
    count_change = len(out) / len(prev) - 1 if len(prev) else 0.0
    m = out.merge(prev[["cb_id", "conv_price_now", "cb_close"]], on="cb_id", how="inner", suffixes=("", "_prev"))
    changed = (m["conv_price_now"] - m["conv_price_now_prev"]).abs() > 1e-6
    eff = m["conv_price_effective"] if "conv_price_effective" in m else pd.Series(None, index=m.index)
    explained = eff.notna() & (eff.astype(str) > prev_day) & (eff.astype(str) <= day)
    unexplained = m[changed & ~explained]
    move = m["cb_close"] / m["cb_close_prev"] - 1
    jumps = m.assign(move=move)[move.abs() > PRICE_JUMP]
    return {"prev_day": prev_day, "count_change": count_change, "unexplained": unexplained, "jumps": jumps}


def save_archive(day, stock_ids):
    """把當天用到的原始資料壓縮存檔（data/archive/<日期>.json.gz），之後可以重現任何一天的計算。
    股價和法人只留有可轉債的股票，檔案才不會太大。"""
    folder = C.RAW_DIR / day
    keep = set(stock_ids)
    bundle = {}
    for name in ["cb_terms", "cb_quotes", "stock_twse", "stock_tpex", "institutional"]:
        p = folder / f"{name}.json"
        if not p.exists():
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        if name == "stock_twse":
            rows = [r for r in rows if str(r.get("Code", "")).strip() in keep]
        elif name == "stock_tpex":
            rows = [r for r in rows if str(r.get("SecuritiesCompanyCode", "")).strip() in keep]
        elif name == "institutional":
            rows = [r for r in rows if str(r.get("stock_id", "")).strip() in keep]
        bundle[name] = rows
    adj = C.DATA_DIR / "conv_price_adjustments.csv"
    if adj.exists():
        bundle["conv_price_adjustments"] = adj.read_text(encoding="utf-8-sig")
    out = C.DATA_DIR / "archive" / f"{day}.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f"  原始資料已存檔：archive/{out.name}（{out.stat().st_size / 1024:.0f} KB）")


def build(day):
    if (C.RAW_DIR / day / "closed.txt").exists():
        print(f"{day} 休市，沒有新資料。")
        save_quality({"date": day, "status": "CLOSED", "warnings": "休市"})
        return None
    raw_twse, raw_tpex = load(day, "stock_twse"), load(day, "stock_tpex")
    check_stock_date(day, raw_twse, "上市")
    check_stock_date(day, raw_tpex, "上櫃")
    terms = pick(load(day, "cb_terms"), C.TERMS_FIELDS)
    # 私募債沒有在市場交易、代號不是 5 碼數字，先排除
    terms["cb_id"] = terms["cb_id"].astype(str).str.strip()
    terms = terms[terms["cb_id"].str.fullmatch(r"\d{5,6}")].copy()
    terms, n_foreign = only_twd(terms)
    terms, n_expired = drop_expired(terms, day)
    quotes = pick(load(day, "cb_quotes"), C.QUOTE_FIELDS).rename(columns={"close": "cb_close"})
    parts = []
    for raw, fields in [(raw_twse, C.TWSE_STOCK_FIELDS), (raw_tpex, C.TPEX_STOCK_FIELDS)]:
        part = pick(raw, fields)
        part["stock_date"] = raw["Date"].map(to_iso) if "Date" in raw else None
        parts.append(part)
    stocks = pd.concat(parts).rename(columns={"close": "stock_close"})

    for df, col in [(terms, "cb_id"), (quotes, "cb_id"), (stocks, "stock_id")]:
        df[col] = df[col].astype(str).str.strip()
    terms, n_code_mismatch = map_stock_id(terms)

    # 同一檔可轉債或股票出現兩次會讓結果表重複，保留第一筆並提醒
    for df, col, label in [(terms, "cb_id", "發行資料"), (quotes, "cb_id", "可轉債行情"), (stocks, "stock_id", "股價")]:
        dup = df[col].duplicated()
        if dup.any():
            print(f"  ⚠ {label}有 {dup.sum()} 筆重複代號（{'、'.join(df.loc[dup, col].head(5))}），只保留第一筆")
    terms = terms.drop_duplicates("cb_id")
    quotes = quotes.drop_duplicates("cb_id")
    stocks = stocks.drop_duplicates("stock_id")

    t = (terms.merge(quotes, on="cb_id", how="left")
              .merge(stocks, on="stock_id", how="left")
              .merge(adjusted_prices(day), on="cb_id", how="left"))

    for col in ["conv_price", "conv_price_adj", "cb_close", "stock_close", "put_price"]:
        if col in t:
            t[col] = to_num(t[col])
    # 有調整過就用最新轉換價，否則用發行時轉換價
    t["conv_price_now"] = t["conv_price_adj"].fillna(t["conv_price"])
    t["conv_value"] = 100 / t["conv_price_now"] * t["stock_close"]
    t["premium"] = t["cb_close"] / t["conv_value"] - 1
    t["date"] = day

    # 提醒欄位：哪些溢價率不能直接拿來判斷套利
    t["is_exchangeable"] = t["cb_name"].astype(str).str.contains("E")  # 交換債，換的是別家股票
    t["conv_not_started"] = pd.to_numeric(t["conv_start"], errors="coerce") > int(day.replace("-", ""))
    t["traded_today"] = t["cb_close"].notna()

    # 資料新鮮度（文件 2.2）：每個數字是哪一天的資料、轉換價從哪裡來
    t["cb_price_date"] = t["traded_today"].map(lambda x: day if x else None)
    t["conv_price_source"] = t["conv_price_adj"].notna().map({True: "MOPS 最新", False: "發行時"})
    t.loc[t["is_exchangeable"], "premium"] = float("nan")

    # 低流動性：成交太少的收盤價參考性低（App 與研究評分要打折看）
    t["volume"] = to_num(t["volume"]) if "volume" in t else float("nan")
    t["low_liquidity"] = t["traded_today"] & (t["volume"] < LOW_VOLUME)

    # 正確性：MOPS 和櫃買中心的發行時轉換價交叉核對
    t, n_issue_mismatch = mops_cross_check(t)

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
    cmp = compare_previous(out, day)
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
    report = health_check(out, n_issue_mismatch, mops_age(day), cmp)
    report.update(date=day, foreign_excluded=n_foreign, expired_excluded=n_expired,
                  code_mismatch=n_code_mismatch)
    save_quality(report)
    save_archive(day, out["stock_id"].dropna().unique())
    return out


# 健康檢查的警戒線（依 2026-10-21 正常資料設定：389 檔、99.5% 有股價、44% 有成交）
MIN_CB_COUNT = 300        # 可轉債總數低於這個數，可能是發行資料沒抓完整
MIN_STOCK_COVERAGE = 0.95  # 有股價的比例
MIN_TRADED_RATIO = 0.15    # 有成交的比例；太低通常是抓到非交易日或行情檔不完整
SUSPICIOUS_DISCOUNT = -0.05  # 折價超過 5% 很少見，多半是轉換價沒更新
EXTREME_PREMIUM = 5.0        # 溢價率超過 500%，可轉債幾乎只剩債券價值


def health_check(out, n_issue_mismatch=0, age=None, cmp=None):
    """印出每日資料健康狀況。⚠ 代表數字不正常，要回頭檢查資料來源。"""
    n = len(out)
    stock_cov = out["stock_close"].notna().mean() if n else 0
    traded = out["traded_today"].mean() if n else 0
    no_conv = out["conv_price_now"].isna() | (out["conv_price_now"] <= 0)
    tradable = out[out["traded_today"] & ~out["conv_not_started"]]
    deep = tradable[tradable["premium"] < SUSPICIOUS_DISCOUNT]
    extreme = (out["premium"] > EXTREME_PREMIUM).sum()

    warnings = []

    def line(ok, text):
        print(f"  {'✓' if ok else '⚠'} {text}")
        if not ok:
            warnings.append(text)

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
    stale = (out["stock_date"].notna() & (out["stock_date"] != out["date"])).sum() if "stock_date" in out else 0
    line(stale == 0, f"股價日期和當天不同的 {stale} 檔")

    # 正確性：MOPS 交叉核對、低流動性
    line(n_issue_mismatch == 0, f"發行時轉換價 MOPS 與櫃買中心不一致的 {n_issue_mismatch} 檔")
    low = int(out["low_liquidity"].sum()) if "low_liquidity" in out else 0
    print(f"  · 成交少於 {LOW_VOLUME} 張（價格參考性低）的 {low} 檔")

    # 及時性：MOPS 多久沒更新
    if age is None:
        line(False, "找不到 MOPS 更新紀錄（請在自己電腦跑 mops_conv_price.py 並推上 GitHub）")
    else:
        line(age <= MOPS_STALE_DAYS, f"MOPS 轉換價 {age} 天前更新（正常 ≤ {MOPS_STALE_DAYS} 天）")

    # 一致性：跟前一個交易日比
    cmp = cmp or {}
    unexplained, jumps = cmp.get("unexplained", pd.DataFrame()), cmp.get("jumps", pd.DataFrame())
    if cmp.get("prev_day"):
        cc = cmp["count_change"]
        line(abs(cc) <= 0.05, f"可轉債檔數比 {cmp['prev_day']} 變動 {cc:+.1%}（正常 ±5% 內）")
        line(unexplained.empty, f"轉換價有變但沒有生效紀錄的 {len(unexplained)} 檔")
        for _, r in unexplained.head(5).iterrows():
            print(f"    - {r['cb_id']} {str(r['cb_name']).strip()}：{r['conv_price_now_prev']:g} → {r['conv_price_now']:g}")
        print(f"  · 可轉債價格單日變動超過 {PRICE_JUMP:.0%} 的 {len(jumps)} 檔")
        for _, r in jumps.head(5).iterrows():
            print(f"    - {r['cb_id']} {str(r['cb_name']).strip()}：{r['cb_close_prev']:g} → {r['cb_close']:g}（{r['move']:+.1%}）")
    return {
        "cb_count": n, "stock_coverage": round(stock_cov, 4), "traded_ratio": round(traded, 4),
        "traded_count": int(out["traded_today"].sum()), "missing_conv_price": int(no_conv.sum()),
        "deep_discount": len(deep), "extreme_premium": int(extreme), "stale_stock_date": int(stale),
        "mops_adjusted": int((out.get("conv_price_source") == "MOPS 最新").sum()),
        "issue_price_mismatch": int(n_issue_mismatch), "low_liquidity": low,
        "mops_age_days": "" if age is None else age,
        "count_change": round(cmp.get("count_change", 0.0), 4) if cmp.get("prev_day") else "",
        "unexplained_conv_change": len(unexplained), "price_jumps": len(jumps),
        "status": "OK" if not warnings else "WARN", "warnings": "；".join(warnings),
    }


def save_quality(report):
    """每天一列，存成 data/quality_report.csv，App 的「資料品質頁」會讀這個檔。"""
    path = C.DATA_DIR / "quality_report.csv"
    old = pd.read_csv(path, dtype={"date": str}, encoding="utf-8-sig") if path.exists() else pd.DataFrame()
    if not old.empty:
        old = old[old["date"] != report["date"]]
    cols = ["date", "status", "cb_count", "traded_count", "traded_ratio", "stock_coverage",
            "missing_conv_price", "deep_discount", "extreme_premium", "stale_stock_date",
            "mops_adjusted", "foreign_excluded", "expired_excluded", "code_mismatch",
            "issue_price_mismatch", "low_liquidity", "mops_age_days", "count_change",
            "unexplained_conv_change", "price_jumps", "warnings"]
    new = pd.DataFrame([{c: report.get(c, "") for c in cols}])
    pd.concat([old, new], ignore_index=True).sort_values("date").to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  品質報告已存到 {path.name}（{report['status']}）")


def _has_table(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='daily'").fetchone() is not None


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat())