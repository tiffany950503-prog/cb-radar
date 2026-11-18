"""可轉債與普通股的分析指標（給 App「指標分析」頁使用）。

只用 data/daily_<日期>.csv 裡已經有的欄位推算，不另外抓資料。
每個指標的公式都寫在 INDICATORS，App 會直接顯示這份說明。

單日指標（add_metrics）：只需要當天資料
  價內程度、債股性、賣回收益率、到期收益率、剩餘年限、距賣回天數、成交金額、餘額
歷史指標（history_metrics）：需要累積多天資料
  股票年化波動度、股票區間報酬、可轉債區間報酬、實證 Delta（可轉債對股價的敏感度）
"""
import numpy as np
import pandas as pd

EQUITY_PREMIUM = 0.10   # 溢價率 ≤ 10%：股性
BOND_PREMIUM = 0.40     # 溢價率 ≥ 40%：債性；中間為平衡型
MIN_PAIRS = 8           # 實證 Delta 至少要幾組「兩天都有成交」的報酬
MIN_RETURNS = 8         # 波動度至少要幾個日報酬
TRADING_DAYS = 252
MIN_YIELD_DAYS = 90      # 距離不到 90 天不算年化收益率（年化後數字會被放大到失真）

STYLE_ORDER = ["股性", "平衡", "債性", "無法判斷"]

# 指標說明：(名稱, 類別, 公式, 怎麼解讀)
INDICATORS = [
    ("溢價率", "可轉債", "可轉債價 ÷ 轉換價值 − 1",
     "越低越貼近股票；負數是折價。高溢價代表多付了「下檔保護」的成本。"),
    ("轉換價值（Parity）", "可轉債", "100 ÷ 轉換價 × 股價",
     "現在轉換成股票值多少（每 100 元面額）。"),
    ("價內程度", "可轉債＋股票", "股價 ÷ 轉換價 − 1",
     "正數＝股價已高於轉換價（價內），轉換有利；負數＝價外，價格主要靠債券價值支撐。"),
    ("債股性", "可轉債", f"溢價率 ≤ {EQUITY_PREMIUM:.0%} 股性、≥ {BOND_PREMIUM:.0%} 債性，其餘平衡",
     "股性：跟著股價漲跌；債性：價格貼近債券價值、跌幅有限；平衡：兩者兼具。"),
    ("賣回收益率（YTP）", "可轉債（債券面）", "(賣回價 ÷ 可轉債價)^(1 ÷ 距賣回年數) − 1",
     "現在買進、在賣回日賣回給公司的年化報酬，是「退可守」的量化。台灣可轉債多為零息，所以不計票息；距賣回不到 90 天不計算。"),
    ("到期收益率（YTM）", "可轉債（債券面）", "(100 ÷ 可轉債價)^(1 ÷ 剩餘年數) − 1",
     "假設持有到期、以面額 100 還本的年化報酬。部分可轉債到期另有補償金，實際可能較高；剩不到 90 天不計算。"),
    ("剩餘年限", "可轉債", "（到期日 − 資料日）÷ 365", "越短，價格越會往到期還本金額靠攏。"),
    ("成交金額", "流動性", "成交張數 × 可轉債價 × 1,000 元", "一張面額 10 萬元；成交金額太小，收盤價參考性低。"),
    ("餘額", "流動性", "目前流通在外面額（億元）", "餘額越小，市場越不活絡，也代表多數已被轉換或賣回。"),
    ("股票年化波動度", "普通股", "日對數報酬標準差 × √252",
     "股價波動越大，轉換權（選擇權）越值錢，通常支撐較高的溢價率。"),
    ("區間報酬", "普通股／可轉債", "最後一天價格 ÷ 第一天價格 − 1", "比較同一期間股票和可轉債誰漲得多。"),
    ("實證 Delta", "可轉債＋股票", "可轉債日報酬對股票日報酬的迴歸斜率",
     "股價漲 1%，可轉債平均漲幾 %。接近 1 是股性、接近 0 是債性；只用兩天都有成交的日子計算。"),
    ("法人 5 日買超", "普通股", "外資＋投信＋自營商近 5 個交易日買賣超（張）", "反映籌碼動向，可搭配溢價率觀察。"),
]


def to_date(s):
    """20270211、20270211.0、2027-02-11 都轉成日期；空值回傳 NaT。"""
    s = pd.Series(s).astype(str).str.replace(r"\.0$", "", regex=True).str.replace("-", "")
    return pd.to_datetime(s.where(s.str.fullmatch(r"\d{8}")), format="%Y%m%d", errors="coerce")


def _annual_yield(target, price, years):
    ok = (price > 0) & (target > 0) & (years >= MIN_YIELD_DAYS / 365)
    out = pd.Series(np.nan, index=price.index)
    out[ok] = (target[ok] / price[ok]) ** (1 / years[ok]) - 1
    return out


def style_of(premium):
    return np.select([premium.isna(), premium <= EQUITY_PREMIUM, premium >= BOND_PREMIUM],
                     ["無法判斷", "股性", "債性"], "平衡")


def add_metrics(df, day):
    """在當天結果表加上單日指標欄位，回傳新的 DataFrame。"""
    d = df.copy()
    today = pd.Timestamp(day)
    premium = d["premium"].where(~d["is_exchangeable"])
    d["moneyness"] = (d["stock_close"] / d["conv_price_now"] - 1).where(~d["is_exchangeable"])
    d["cb_style"] = style_of(premium)

    maturity, put_date = to_date(d["maturity"]).values, to_date(d["put_date"]).values
    d["years_left"] = (pd.Series(maturity, index=d.index) - today).dt.days / 365
    put_days = (pd.Series(put_date, index=d.index) - today).dt.days
    d["put_days_left"] = put_days.where(put_days > 0)
    put_price = d["put_price"].where(d["put_price"] > 0)
    d["ytp"] = _annual_yield(put_price, d["cb_close"], d["put_days_left"] / 365)
    d["ytm"] = _annual_yield(pd.Series(100.0, index=d.index), d["cb_close"], d["years_left"])
    d["turnover"] = d["volume"] * d["cb_close"] * 1000          # 元
    d["balance_100m"] = d["balance"] / 1e8                       # 億元
    return d


def history_metrics(hist):
    """用多天資料算歷史指標，每檔可轉債一列（index = cb_id）。"""
    if hist.empty:
        return pd.DataFrame()
    h = hist.sort_values("date")
    dates = sorted(h["date"].unique())

    # 股票：同一檔股票可能有好幾檔可轉債，先去重
    stock = (h.dropna(subset=["stock_close"]).drop_duplicates(["stock_id", "date"])
             .pivot(index="date", columns="stock_id", values="stock_close").reindex(dates))
    stock_ret = np.log(stock).diff()
    vol = stock_ret.std() * np.sqrt(TRADING_DAYS)
    vol[stock_ret.count() < MIN_RETURNS] = np.nan
    stock_period = stock.ffill().iloc[-1] / stock.bfill().iloc[0] - 1

    # 可轉債：只有成交的日子才有價格；沒成交的日子留空，不補值
    cb = h.pivot_table(index="date", columns="cb_id", values="cb_close").reindex(dates)
    cb_ret = np.log(cb).diff()
    cb_period = cb.ffill().iloc[-1] / cb.bfill().iloc[0] - 1

    link = h.drop_duplicates("cb_id", keep="last").set_index("cb_id")["stock_id"]
    rows = []
    for cb_id, stock_id in link.items():
        delta, pairs = np.nan, 0
        if cb_id in cb_ret and stock_id in stock_ret:
            pair = pd.concat([cb_ret[cb_id], stock_ret[stock_id]], axis=1).dropna()
            pairs = len(pair)
            if pairs >= MIN_PAIRS and pair.iloc[:, 1].var() > 0:
                delta = pair.iloc[:, 0].cov(pair.iloc[:, 1]) / pair.iloc[:, 1].var()
        rows.append({"cb_id": cb_id, "stock_vol": vol.get(stock_id, np.nan),
                     "stock_period_ret": stock_period.get(stock_id, np.nan),
                     "cb_period_ret": cb_period.get(cb_id, np.nan),
                     "emp_delta": delta, "delta_pairs": pairs})
    return pd.DataFrame(rows).set_index("cb_id")


def market_series(hist):
    """全市場每日統計：溢價率中位數、折價檔數、股性/債性占比等。"""
    rows = []
    for day, g in hist.groupby("date"):
        p = g.loc[g["traded_today"] & ~g["is_exchangeable"], "premium"].dropna()
        conv = g["traded_today"] & ~g["conv_not_started"] & ~g["is_exchangeable"]
        style = pd.Series(style_of(p), index=p.index)
        rows.append({"date": day, "溢價率中位數": p.median(),
                     "折價檔數（已可轉換）": int((g.loc[conv, "premium"] < 0).sum()),
                     "有成交檔數": int(g["traded_today"].sum()),
                     "股性占比": (style == "股性").mean() if len(p) else np.nan,
                     "平衡占比": (style == "平衡").mean() if len(p) else np.nan,
                     "債性占比": (style == "債性").mean() if len(p) else np.nan})
    return pd.DataFrame(rows).set_index("date").sort_index()
