"""可轉債雷達：研究 App 第一版（Streamlit）。

在自己電腦執行：
  pip install -r requirements.txt
  streamlit run app.py

只讀 data/ 裡已經算好的結果，不重新計算任何數字。
"""
from pathlib import Path

import pandas as pd
import altair as alt
import streamlit as st

import alerts
import metrics as M

DATA = Path(__file__).parent / "data"

st.set_page_config(page_title="可轉債雷達", page_icon="📈", layout="wide")


# ── 讀資料 ──────────────────────────────────────────────
@st.cache_data(ttl=600)
def available_days():
    return sorted(f.stem.replace("daily_", "") for f in DATA.glob("daily_*.csv"))


@st.cache_data(ttl=600)
def load_day(day):
    df = pd.read_csv(DATA / f"daily_{day}.csv", dtype={"cb_id": str, "stock_id": str}, encoding="utf-8-sig")
    for col in ["traded_today", "conv_not_started", "is_exchangeable", "code_mismatch", "low_liquidity"]:
        if col in df:
            df[col] = df[col].astype(str).str.lower().isin(["true", "1"])
        else:
            df[col] = False
    df["cb_name"] = df["cb_name"].astype(str).str.strip()
    return df


@st.cache_data(ttl=600)
def load_history():
    frames = [load_day(d).assign(date=d) for d in available_days()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@st.cache_data(ttl=600)
def load_history_metrics():
    return M.history_metrics(load_history())


@st.cache_data(ttl=600)
def load_market_series():
    return M.market_series(load_history())


@st.cache_data(ttl=600)
def load_quality():
    p = DATA / "quality_report.csv"
    return pd.read_csv(p, dtype={"date": str}, encoding="utf-8-sig") if p.exists() else pd.DataFrame()


@st.cache_data(ttl=600)
def load_fetch_log():
    p = DATA / "fetch_log.csv"
    return pd.read_csv(p, dtype=str, encoding="utf-8-sig") if p.exists() else pd.DataFrame()


def line_chart(data, title, fmt=",.2f"):
    """折線圖：Y 軸依數字範圍調整，不從 0 開始，變化才看得出來。data 的 index 是日期。"""
    long = data.reset_index(names="日期").melt("日期", var_name="項目", value_name="數值").dropna()
    chart = (alt.Chart(long).mark_line(point=True)
             .encode(x=alt.X("日期:N", title=None),
                     y=alt.Y("數值:Q", title=None, scale=alt.Scale(zero=False)),
                     color=alt.Color("項目:N", legend=alt.Legend(orient="bottom", title=None)),
                     tooltip=["日期", "項目", alt.Tooltip("數值:Q", format=fmt)])
             .properties(height=260))
    st.caption(title)
    st.altair_chart(chart, width="stretch")


def pct(x, digits=1):
    return "—" if pd.isna(x) else f"{x:+.{digits}%}"


def num(x, fmt="{:,.2f}"):
    return "—" if pd.isna(x) else fmt.format(x)


def go_to_detail(cb_id):
    st.session_state.cb = cb_id
    st.session_state.page = "個股頁"


# ── 側邊欄 ──────────────────────────────────────────────
days = available_days()
if not days:
    st.error("data 資料夾裡還沒有 daily_*.csv。請先執行 fetch.py 和 build.py。")
    st.stop()

PAGES = ["首頁", "新手專區", "可轉債清單", "指標分析", "個股頁", "資料品質", "方法說明"]
st.session_state.setdefault("page", "首頁")
with st.sidebar:
    st.title("可轉債雷達")
    st.radio("頁面", PAGES, key="page", label_visibility="collapsed")
    day = st.selectbox("資料日期", days[::-1], index=0)
    st.caption("每個交易日 17:30 自動更新。僅供研究，不構成投資建議。")

df = load_day(day)
quality = load_quality()
q_today = quality[quality["date"] == day].iloc[0] if not quality.empty and (quality["date"] == day).any() else None


# ── 首頁 ────────────────────────────────────────────────
def page_home():
    st.header(f"{day} 盤後")
    disc, inst, put = alerts.find_alerts(df, day)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("上市櫃可轉債", f"{len(df)} 檔")
    c2.metric("今天有成交", f"{int(df['traded_today'].sum())} 檔")
    c3.metric("折價且可轉換", f"{len(disc)} 檔")
    status = q_today["status"] if q_today is not None else "未知"
    c4.metric("資料品質", {"OK": "正常", "WARN": "有警告", "CLOSED": "休市"}.get(status, status))
    if q_today is not None and status == "WARN":
        st.warning(f"今天的資料有警告：{q_today['warnings']}。數字請搭配「資料品質」頁判讀。")

    st.subheader("今日訊號")
    st.caption(f"條件：溢價率 < {alerts.PREMIUM_MAX:.0%}、成交 ≥ {alerts.MIN_VOLUME} 張、已可轉換；"
               f"法人 5 日買超 > {alerts.INST_NET_MIN} 張；賣回日 {alerts.PUT_WITHIN_DAYS} 天內")
    tabs = st.tabs([f"折價（{len(disc)}）", f"法人買超（{len(inst)}）", f"近賣回日（{len(put)}）"])
    specs = [
        (disc, {"premium": "溢價率", "volume": "成交（張）", "cb_close": "可轉債價", "stock_close": "股價"}),
        (inst, {"inst_net_5d": "法人 5 日買超（張）", "premium": "溢價率", "cb_close": "可轉債價"}),
        (put, {"days_left": "剩幾天", "put_date": "賣回日", "put_price": "賣回價", "cb_close": "可轉債價"}),
    ]
    for i, (tab, (rows, cols)) in enumerate(zip(tabs, specs)):
        with tab:
            if rows.empty:
                st.info("今天沒有符合條件的可轉債。")
                continue
            show = rows[["cb_id", "cb_name"] + [c for c in cols if c in rows]].rename(
                columns={"cb_id": "代號", "cb_name": "名稱", **cols})
            if "溢價率" in show:
                show["溢價率"] = show["溢價率"].map(pct)
            st.dataframe(show, hide_index=True, width="stretch")
            pick = st.selectbox("查看個股頁", rows["cb_id"] + " " + rows["cb_name"], key=f"pick-{i}")
            st.button("前往", key=f"go-{i}", on_click=go_to_detail, args=(pick.split()[0],))


# ── 新手專區 ────────────────────────────────────────────
def page_beginner():
    st.header("新手專區")
    st.write("第一次接觸可轉債嗎？這一頁用白話說明基本概念，並用今天的真實資料一步步算一次。")

    st.subheader("1. 可轉債是什麼？")
    st.markdown("""
**可轉換公司債（可轉債，CB）** 是公司發行的一種債券，但多了一個權利：
**在約定的期間內，可以用事先約定的價格，把債券換成這家公司的股票。**

- **股價漲很多時**：換成股票，賺股價上漲的錢
- **股價跌時**：不換，繼續當債券，到期或賣回時公司會還本金（前提是公司沒倒）

所以常被形容成「**進可攻、退可守**」。不過「守」的前提是公司還得出錢，這也是為什麼要看發行公司的體質。

在台灣，可轉債的代號是「股票代號＋1 碼」，例如華星光（4979）發行的第三次可轉債叫「華星光三」，代號 49793。
報價以「每 100 元面額」表示，一張面額 10 萬元；報價 120，代表一張要 12 萬元。
""")

    st.subheader("2. 名詞小辭典")
    terms = {
        "轉換價": "多少錢的股價可以換一股。轉換價 50 元，代表每 50 元面額可以換 1 股。公司配股配息後，轉換價會依規定往下調整。",
        "轉換價值": "現在把可轉債換成股票，值多少錢。公式：100 ÷ 轉換價 × 股價。",
        "溢價率": "可轉債價格比轉換價值貴多少。正的叫「溢價」（比換成股票貴），負的叫「折價」（比換成股票便宜）。",
        "轉換期間": "可以申請轉換的時間範圍。剛發行的可轉債通常要等一段時間才能轉換。",
        "賣回權": "持有人在約定日期，可以用約定價格把可轉債賣回給公司，是「退可守」的保障之一。",
        "到期日": "沒有轉換、也沒有賣回的話，公司會在到期日還本金。",
        "交換債": "名稱裡有 E 的，換的是「別家公司」的股票，不能用發行公司的股價算溢價率。",
        "債性／股性": "溢價率很高時，價格主要跟著「債券」的價值走（債性）；溢價率接近 0 時，價格會跟著股價漲跌（股性）。",
        "CBAS": "可轉債資產交換，把可轉債拆成債券與選擇權兩部分分開賣，讓投資人用較少資金參與。本系統目前沒有涵蓋。",
    }
    for k, v in terms.items():
        with st.expander(k):
            st.write(v)

    st.subheader("3. 自己算算看")
    st.caption("改動下面的數字，看看轉換價值和溢價率怎麼變。")
    c1, c2, c3 = st.columns(3)
    cb = c1.number_input("可轉債價格（每 100 元面額）", min_value=1.0, value=110.0, step=1.0, key="calc-cb")
    cp = c2.number_input("轉換價（元）", min_value=0.1, value=50.0, step=1.0, key="calc-cp")
    sp = c3.number_input("股價（元）", min_value=0.1, value=55.0, step=1.0, key="calc-sp")
    shares = 100 / cp
    value = shares * sp
    prem = cb / value - 1
    st.markdown(f"""
- 每 100 元面額可以換 **100 ÷ {cp:g} = {shares:.3f} 股**
- 換成股票值 **{shares:.3f} × {sp:g} = {value:.2f} 元**（這就是轉換價值）
- 溢價率 ＝ {cb:g} ÷ {value:.2f} − 1 ＝ **{prem:+.2%}**
""")
    if prem < 0:
        st.success("溢價率是負的：可轉債比換成的股票便宜，叫做「折價」。但折價不等於穩賺，請看下方的常見誤解。")
    elif prem < 0.05:
        st.info("溢價率接近 0：可轉債價格幾乎和換成股票的價值一樣，價格會緊跟股價變動（股性強）。")
    else:
        st.info("溢價率是正的：可轉債比換成的股票貴，多付的部分可以想成是「下檔有保障」的成本。")

    st.subheader("4. 用今天的真實資料算一次")
    tradable = df[df["traded_today"] & ~df["conv_not_started"] & ~df["is_exchangeable"] & df["premium"].notna()]
    if tradable.empty:
        st.info("今天沒有可以拿來示範的可轉債。")
    else:
        ex = tradable.iloc[(tradable["premium"].abs()).argsort().iloc[0]]
        e_shares = 100 / ex["conv_price_now"]
        st.markdown(f"""
以 **{ex['cb_name']}（{ex['cb_id']}）** 為例，{day} 收盤時：

| 步驟 | 計算 | 結果 |
| --- | --- | --- |
| 可轉債價格 | 收盤價 | {ex['cb_close']:g} |
| 每 100 元可換幾股 | 100 ÷ 轉換價 {ex['conv_price_now']:g} | {e_shares:.3f} 股 |
| 轉換價值 | {e_shares:.3f} × 股價 {ex['stock_close']:g} | {ex['conv_value']:.2f} |
| 溢價率 | {ex['cb_close']:g} ÷ {ex['conv_value']:.2f} − 1 | {ex['premium']:+.2%} |

轉換價來源：{ex.get('conv_price_source', '—')}。
""")
        st.button(f"看 {ex['cb_name']} 的個股頁", on_click=go_to_detail, args=(ex["cb_id"],))

    st.subheader("5. 常見誤解：折價不等於穩賺")
    st.markdown("""
看到溢價率是負的，直覺會想「買可轉債、馬上轉成股票賣掉就賺到了」。實際上還有這些問題：

- **交易成本**：買賣都有手續費與稅費，折價太小時可能被成本吃掉。
- **轉換要時間**：申請轉換到拿到股票需要幾個交易日，這段期間股價可能下跌。要鎖住價差，通常得同時放空股票，而放空有額度與成本的限制。
- **流動性**：很多可轉債一天只成交幾張，甚至沒有成交，想買不一定買得到。本系統把成交少於 10 張的標記為「價格參考性低」。
- **停止轉換期間**：公司除權息、開股東會前後會暫停轉換，這段期間的折價無法馬上利用。
- **公司體質**：折價有時是市場在反映公司的財務風險，不一定是撿到便宜。
- **資料延遲**：本系統用收盤後資料，隔天開盤價格可能已經不同。
""")

    st.subheader("6. 這個 App 怎麼用")
    st.markdown("""
| 想做什麼 | 去哪一頁 |
| --- | --- |
| 看今天有哪些值得注意的可轉債 | **首頁**的「今日訊號」 |
| 依條件找可轉債、比較 | **可轉債清單**，可以搜尋、篩選溢價率 |
| 深入看某一檔 | **個股頁**：條件、走勢、研究評分、資料來源 |
| 確認今天的資料可不可信 | **資料品質** |
| 了解公式與資料來源 | **方法說明** |
""")
    st.caption("本系統為學生研究專案，內容僅供學習與研究，不構成任何投資建議。")


# ── 可轉債清單 ──────────────────────────────────────────
def page_list():
    st.header("可轉債清單")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
    q = c1.text_input("搜尋代號或名稱", placeholder="例如 4979、華星光")
    only_traded = c2.checkbox("只看有成交", value=True)
    only_conv = c3.checkbox("只看已可轉換", value=True)
    lo, hi = c4.slider("溢價率範圍（%）", -20, 100, (-20, 30))

    view = df.copy()
    if q:
        view = view[view["cb_id"].str.contains(q) | view["cb_name"].str.contains(q) | view["stock_id"].str.contains(q)]
    if only_traded:
        view = view[view["traded_today"]]
    if only_conv:
        view = view[~view["conv_not_started"]]
    prem = view["premium"] * 100
    keep = prem.between(lo, hi)
    if not only_traded:  # 沒成交的沒有溢價率，不勾「只看有成交」時一起列出
        keep |= prem.isna()
    view = view[keep]

    cols = {"cb_id": "代號", "cb_name": "名稱", "stock_id": "股票", "cb_close": "可轉債價", "stock_close": "股價",
            "conv_price_now": "轉換價", "premium": "溢價率（%）", "volume": "成交（張）",
            "inst_net_5d": "法人 5 日（張）", "conv_price_source": "轉換價來源"}
    view = view.copy()
    view["提醒"] = [
        "、".join(x for x in [
            "成交少" if lowliq else "",
            "轉換價核對不一致" if check == "不一致" else "",
            "未開始轉換" if notstart else "",
        ] if x) for lowliq, check, notstart in zip(
            view["low_liquidity"], view.get("issue_price_check", pd.Series("", index=view.index)),
            view["conv_not_started"])]
    cols["提醒"] = "提醒"
    show = view[[c for c in cols if c in view]].rename(columns=cols)
    show["溢價率（%）"] = show["溢價率（%）"] * 100
    st.caption(f"共 {len(show)} 檔，點表格任一列可選取，再按下方按鈕看個股頁。"
               "「成交少」代表當天成交不到 10 張，收盤價參考性低。")
    event = st.dataframe(
        show, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={"溢價率（%）": st.column_config.NumberColumn(format="%.2f"),
                       "法人 5 日（張）": st.column_config.NumberColumn(format="%.0f")})
    rows = event.selection.rows if event and event.selection else []
    if rows:
        cb_id = show.iloc[rows[0]]["代號"]
        st.button(f"查看 {cb_id} {show.iloc[rows[0]]['名稱']} 的個股頁", on_click=go_to_detail, args=(cb_id,))


# ── 指標分析 ────────────────────────────────────────────
# 可以拿來畫圖的指標：欄位 → (顯示名稱, 數字格式)
METRIC_OPTS = {
    "premium": ("溢價率", ".1%"),
    "conv_value": ("轉換價值", ",.1f"),
    "cb_close": ("可轉債價", ",.2f"),
    "moneyness": ("價內程度", ".1%"),
    "ytp": ("賣回收益率（YTP）", ".1%"),
    "ytm": ("到期收益率（YTM）", ".1%"),
    "years_left": ("剩餘年限（年）", ".2f"),
    "turnover_m": ("成交金額（百萬元）", ",.1f"),
    "balance_100m": ("餘額（億元）", ",.2f"),
    "stock_vol": ("股票年化波動度", ".1%"),
    "stock_period_ret": ("股票區間報酬", ".1%"),
    "cb_period_ret": ("可轉債區間報酬", ".1%"),
    "emp_delta": ("實證 Delta", ".2f"),
    "inst_net_5d": ("法人 5 日買超（張）", ",.0f"),
}
STYLE_SCALE = alt.Scale(domain=M.STYLE_ORDER, range=["#d94841", "#f2a541", "#3b7dd8", "#b0b0b0"])
TOOLTIP = [alt.Tooltip("cb_name:N", title="名稱"), alt.Tooltip("cb_id:N", title="代號"),
           alt.Tooltip("cb_style:N", title="債股性"),
           alt.Tooltip("cb_close:Q", title="可轉債價", format=",.2f"),
           alt.Tooltip("stock_close:Q", title="股價", format=",.2f"),
           alt.Tooltip("conv_value:Q", title="轉換價值", format=",.1f"),
           alt.Tooltip("premium:Q", title="溢價率", format=".1%"),
           alt.Tooltip("ytp:Q", title="賣回收益率", format=".1%"),
           alt.Tooltip("emp_delta:Q", title="實證 Delta", format=".2f"),
           alt.Tooltip("volume:Q", title="成交（張）", format=",.0f")]


def label(col):
    return METRIC_OPTS[col][0]


def pct_axis(fmt):
    """百分比指標的座標軸顯示成 %，其他用預設格式。"""
    return alt.Axis(format=".0%") if fmt.endswith("%") else alt.Axis()


def axis(col, title=None, log=False, field_type="Q"):
    fmt = METRIC_OPTS[col][1]
    return dict(field=col, type="quantitative" if field_type == "Q" else "nominal",
                title=title or label(col),
                axis=pct_axis(fmt),
                scale=alt.Scale(zero=False, type="symlog" if log else "linear"))


def trim(data, cols, on):
    """排除前後 2% 的極端值，讓圖不會被少數幾檔壓扁。"""
    if not on:
        return data
    keep = pd.Series(True, index=data.index)
    for c in cols:
        lo, hi = data[c].quantile([0.02, 0.98])
        keep &= data[c].between(lo, hi) | data[c].isna()
    return data[keep]


def selected_rows(event, data, brush="brush", point=None):
    """把圖表上的框選／點選結果轉成資料列。"""
    sel = (event or {}).get("selection", {}) if isinstance(event, dict) else getattr(event, "selection", {})
    sel = dict(sel or {})
    if point and sel.get(point):
        ids = {p.get("cb_id") for p in sel[point] if isinstance(p, dict)}
        return data[data["cb_id"].isin(ids)]
    ranges = sel.get(brush) or {}
    if not ranges:
        return data.iloc[0:0]
    keep = pd.Series(True, index=data.index)
    for col, rng in ranges.items():
        if col in data and isinstance(rng, (list, tuple)) and len(rng) == 2:
            keep &= data[col].between(min(rng), max(rng))
    return data[keep]


def show_selection(rows, key):
    if rows.empty:
        st.caption("在圖上按住滑鼠拖曳框選一塊區域，下面就會列出框到的可轉債。")
        return
    cols = {"cb_id": "代號", "cb_name": "名稱", "cb_style": "債股性", "cb_close": "可轉債價", "premium": "溢價率",
            "ytp": "賣回收益率", "emp_delta": "實證 Delta", "volume": "成交（張）"}
    show = rows[list(cols)].rename(columns=cols).sort_values("溢價率")
    st.write(f"框選到 **{len(show)}** 檔")
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "溢價率": st.column_config.NumberColumn(format="percent"),
        "賣回收益率": st.column_config.NumberColumn(format="percent"),
        "實證 Delta": st.column_config.NumberColumn(format="%.2f")})
    pick = st.selectbox("查看個股頁", rows["cb_id"] + " " + rows["cb_name"], key=f"{key}-pick")
    st.button("前往", key=f"{key}-go", on_click=go_to_detail, args=(pick.split()[0],))


def page_metrics():
    st.header("指標分析")
    st.write("把可轉債（債券面＋轉換權）和普通股（波動、籌碼）的指標放在一起看。"
             "圖表都可以互動：滑鼠移上去看數字、拖曳框選、點圖例只看某一類。")

    data = M.add_metrics(df, day).join(load_history_metrics(), on="cb_id")
    data["turnover_m"] = data["turnover"] / 1e6

    with st.expander("篩選條件", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        only_traded = c1.checkbox("只看有成交", value=True, key="m-traded")
        no_exch = c2.checkbox("排除交換債", value=True, key="m-exch")
        only_conv = c3.checkbox("只看已可轉換", value=False, key="m-conv")
        min_vol = c4.number_input("最低成交（張）", 0, 1000, 0, step=10, key="m-vol")
    if only_traded:
        data = data[data["traded_today"]]
    if no_exch:
        data = data[~data["is_exchangeable"]]
    if only_conv:
        data = data[~data["conv_not_started"]]
    if min_vol:
        data = data[data["volume"].fillna(0) >= min_vol]
    if data.empty:
        st.info("沒有符合篩選條件的可轉債。")
        return

    # 指標總覽
    counts = data["cb_style"].value_counts()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("檔數", f"{len(data)}")
    c2.metric("溢價率中位數", pct(data["premium"].median()))
    c3.metric("股性", f"{counts.get('股性', 0)} 檔", help=f"溢價率 ≤ {M.EQUITY_PREMIUM:.0%}")
    c4.metric("平衡", f"{counts.get('平衡', 0)} 檔")
    c5.metric("債性", f"{counts.get('債性', 0)} 檔", help=f"溢價率 ≥ {M.BOND_PREMIUM:.0%}")
    c6.metric("實證 Delta 中位數", num(data["emp_delta"].median()))
    n_days = len(available_days())
    st.caption(f"歷史指標（波動度、區間報酬、實證 Delta）用目前累積的 {n_days} 個交易日計算，"
               f"資料天數越多越穩定。實證 Delta 至少要 {M.MIN_PAIRS} 組「兩天都有成交」的報酬才計算。")

    tab_map, tab_xy, tab_dist, tab_rank, tab_trend, tab_def = st.tabs(
        ["股債性地圖", "自訂散佈圖", "分布", "排行", "市場趨勢", "指標說明"])
    style_pick = alt.selection_point(name="style_pick", fields=["cb_style"], bind="legend")
    color = alt.Color("cb_style:N", title="債股性", scale=STYLE_SCALE, legend=alt.Legend(orient="top"))
    fade = alt.condition(style_pick, alt.value(0.8), alt.value(0.08))

    # 1. 股債性地圖：可轉債價 vs 轉換價值
    with tab_map:
        st.caption("橫軸是轉換價值、縱軸是可轉債價。虛線上的點溢價率 = 0；越往右上越貼著虛線（股性），"
                   "左邊的點價格被債券價值撐在 100 附近（債性）。泡泡大小是成交金額。")
        pts = data.dropna(subset=["conv_value", "cb_close"])
        pts = trim(pts, ["conv_value", "cb_close"], st.checkbox("排除極端值（前後 2%）", True, key="map-trim"))
        mode = st.radio("滑鼠操作", ["框選列出名單", "縮放平移"], horizontal=True, key="map-mode")
        brush = alt.selection_interval(name="brush", encodings=["x", "y"])
        lo = float(min(pts["conv_value"].min(), pts["cb_close"].min()))
        hi = float(max(pts["conv_value"].max(), pts["cb_close"].max()))
        diag = alt.Chart(pd.DataFrame({"v": [lo, hi]})).mark_line(strokeDash=[5, 4], color="gray").encode(
            x="v:Q", y="v:Q")
        floor = alt.Chart(pd.DataFrame({"y": [100]})).mark_rule(color="#888", strokeDash=[2, 2]).encode(y="y:Q")
        dots = alt.Chart(pts).mark_circle(stroke="white", strokeWidth=0.5).encode(
            x=alt.X(**axis("conv_value")), y=alt.Y(**axis("cb_close")), color=color, opacity=fade,
            size=alt.Size("turnover_m:Q", title="成交金額（百萬）", scale=alt.Scale(range=[15, 700]), legend=None),
            tooltip=TOOLTIP)
        if mode == "框選列出名單":
            chart = alt.layer(dots.add_params(brush, style_pick), diag, floor).properties(height=480)
            event = st.altair_chart(chart, width="stretch", on_select="rerun", key="map-chart")
            show_selection(selected_rows(event, pts), "map")
        else:
            chart = alt.layer(dots.add_params(style_pick), diag, floor).properties(height=480).interactive()
            st.altair_chart(chart, width="stretch")

    # 2. 自訂散佈圖：任選兩個指標
    with tab_xy:
        keys = list(METRIC_OPTS)
        c1, c2, c3 = st.columns([2, 2, 1])
        x = c1.selectbox("橫軸", keys, index=keys.index("stock_vol"), format_func=label, key="xy-x")
        y = c2.selectbox("縱軸", keys, index=keys.index("premium"), format_func=label, key="xy-y")
        log = c3.checkbox("壓縮極端值（對數刻度）", key="xy-log")
        pts = data.dropna(subset=[x, y])
        pts = trim(pts, [x, y], st.checkbox("排除極端值（前後 2%）", True, key="xy-trim"))
        st.caption(f"{len(pts)} 檔同時有「{label(x)}」和「{label(y)}」。虛線是趨勢線（線性迴歸）。")
        if pts.empty:
            st.info("這兩個指標目前沒有共同的資料。歷史指標需要多累積幾天。")
        else:
            corr = pts[x].corr(pts[y])
            if pd.notna(corr):
                st.write(f"相關係數：**{corr:+.2f}**（接近 ±1 代表兩個指標關係很強，接近 0 代表沒什麼關係）")
            brush = alt.selection_interval(name="brush", encodings=["x", "y"])
            base = alt.Chart(pts)
            dots = base.mark_circle(size=70, stroke="white", strokeWidth=0.5).encode(
                x=alt.X(**axis(x, log=log)), y=alt.Y(**axis(y, log=log)), color=color, opacity=fade,
                tooltip=TOOLTIP + [alt.Tooltip(f"{x}:Q", title=label(x), format=METRIC_OPTS[x][1]),
                                   alt.Tooltip(f"{y}:Q", title=label(y), format=METRIC_OPTS[y][1])])
            trend = base.transform_regression(x, y).mark_line(color="gray", strokeDash=[5, 4]).encode(
                x=f"{x}:Q", y=f"{y}:Q")
            chart = alt.layer(dots.add_params(brush, style_pick), trend).properties(height=460)
            event = st.altair_chart(chart, width="stretch", on_select="rerun", key="xy-chart")
            show_selection(selected_rows(event, pts), "xy")

    # 3. 分布：直方圖＋各類別箱型圖
    with tab_dist:
        m = st.selectbox("指標", list(METRIC_OPTS), index=0, format_func=label, key="dist-m")
        vals = trim(data.dropna(subset=[m]), [m], st.checkbox("排除極端值（前後 2%）", True, key="dist-trim"))
        if vals.empty:
            st.info("這個指標目前沒有資料。")
        else:
            q = vals[m].quantile([0.25, 0.5, 0.75])
            fmt = METRIC_OPTS[m][1]
            f = (lambda v: f"{v:{fmt}}")
            st.write(f"{len(vals)} 檔：第 25 百分位 **{f(q[0.25])}**、中位數 **{f(q[0.5])}**、第 75 百分位 **{f(q[0.75])}**")
            hist = alt.Chart(vals).mark_bar().encode(
                x=alt.X(f"{m}:Q", bin=alt.Bin(maxbins=40), title=label(m),
                        axis=pct_axis(fmt)),
                y=alt.Y("count():Q", title="檔數", stack=True), color=color, opacity=fade,
                tooltip=[alt.Tooltip("cb_style:N", title="債股性"), alt.Tooltip("count():Q", title="檔數")]
            ).add_params(style_pick).properties(height=300)
            box = alt.Chart(vals).mark_boxplot(extent=1.5, size=28).encode(
                x=alt.X(f"{m}:Q", title=label(m), axis=pct_axis(fmt)),
                y=alt.Y("cb_style:N", title=None, sort=M.STYLE_ORDER),
                color=alt.Color("cb_style:N", scale=STYLE_SCALE, legend=None)).properties(height=180)
            st.altair_chart(hist, width="stretch")
            st.caption("箱型圖：各類別的分布。盒子是中間 50%，中線是中位數，點是離群值。")
            st.altair_chart(box, width="stretch")

    # 4. 排行
    with tab_rank:
        c1, c2, c3 = st.columns([2, 1, 1])
        m = c1.selectbox("依哪個指標排行", list(METRIC_OPTS), index=list(METRIC_OPTS).index("ytp"),
                         format_func=label, key="rank-m")
        desc = c2.radio("順序", ["由高到低", "由低到高"], horizontal=True, key="rank-dir") == "由高到低"
        n = c3.slider("顯示幾檔", 5, 40, 15, key="rank-n")
        top = data.dropna(subset=[m]).sort_values(m, ascending=not desc).head(n).copy()
        if top.empty:
            st.info("這個指標目前沒有資料。")
        else:
            top["label"] = top["cb_name"] + "（" + top["cb_id"] + "）"
            click = alt.selection_point(name="pick", fields=["cb_id"])
            fmt = METRIC_OPTS[m][1]
            bars = alt.Chart(top).mark_bar().encode(
                x=alt.X(f"{m}:Q", title=label(m), axis=pct_axis(fmt)),
                y=alt.Y("label:N", title=None, sort="-x" if desc else "x", axis=alt.Axis(labelLimit=220)),
                color=color,
                opacity=alt.condition(click, alt.value(1), alt.value(0.35)),
                tooltip=TOOLTIP + [alt.Tooltip(f"{m}:Q", title=label(m), format=fmt)]
            ).add_params(click).properties(height=alt.Step(26))
            event = st.altair_chart(bars, width="stretch", on_select="rerun", key="rank-chart")
            rows = selected_rows(event, top, point="pick")
            if rows.empty:
                st.caption("點一下長條可以選取，再前往個股頁。")
            else:
                r = rows.iloc[0]
                st.button(f"查看 {r['cb_name']}（{r['cb_id']}）的個股頁", on_click=go_to_detail,
                          args=(r["cb_id"],), key="rank-go")
            if m == "ytp":
                st.warning("賣回收益率特別高，通常代表市場擔心公司的還款能力（價格被壓低），"
                           "不是單純的便宜。請搭配成交量與公司體質判斷。")

    # 5. 市場趨勢
    with tab_trend:
        ms = load_market_series()
        if len(ms) < 2:
            st.info("累積兩天以上資料後會出現趨勢圖。")
        else:
            ms = ms.reset_index().rename(columns={"date": "日期"})
            hover = alt.selection_point(name="hover", fields=["日期"], nearest=True, on="pointerover", empty=False)
            base = alt.Chart(ms).encode(x=alt.X("日期:N", title=None))
            line = base.mark_line(color="#d94841", point=alt.OverlayMarkDef(color="#d94841")).encode(
                y=alt.Y("溢價率中位數:Q", axis=alt.Axis(format=".0%"), scale=alt.Scale(zero=False)))
            bars = base.mark_bar(opacity=0.3, color="#3b7dd8").encode(
                y=alt.Y("折價檔數（已可轉換）:Q", title="折價檔數（已可轉換）"))
            rule = base.mark_rule(color="gray").encode(
                opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
                tooltip=["日期", alt.Tooltip("溢價率中位數:Q", format=".1%"), "折價檔數（已可轉換）", "有成交檔數"]
            ).add_params(hover)
            st.caption("藍柱：已可轉換且折價的檔數（左軸）；紅線：全市場溢價率中位數（右軸）。滑鼠移到某一天可看數字。"
                       "溢價率中位數下降、折價檔數增加，通常代表股市偏強或可轉債相對便宜。")
            st.altair_chart(alt.layer(bars, line, rule).resolve_scale(y="independent").properties(height=300),
                            width="stretch")
            share = ms.melt("日期", ["股性占比", "平衡占比", "債性占比"], var_name="類別", value_name="占比").dropna()
            share["類別"] = share["類別"].str.replace("占比", "")
            area = alt.Chart(share).mark_area().encode(
                x=alt.X("日期:N", title=None),
                y=alt.Y("占比:Q", stack="normalize", axis=alt.Axis(format=".0%"), title="占比"),
                color=alt.Color("類別:N", scale=STYLE_SCALE, sort=M.STYLE_ORDER, legend=alt.Legend(orient="top")),
                order=alt.Order("類別:N"),
                tooltip=["日期", "類別", alt.Tooltip("占比:Q", format=".1%")]).properties(height=240)
            st.caption("有成交的可轉債中，股性、平衡、債性各占多少。股性比例上升通常代表股價普遍上漲。")
            st.altair_chart(area, width="stretch")

    # 6. 指標說明
    with tab_def:
        st.dataframe(pd.DataFrame(M.INDICATORS, columns=["指標", "類別", "公式", "怎麼解讀"]),
                     hide_index=True, width="stretch")
        st.caption("債股性的分界（10%、40%）是本專案為了方便觀察設定的門檻，不是官方定義；可以在 metrics.py 調整。"
                   "本頁僅供研究，不構成投資建議。")


# ── 個股頁 ──────────────────────────────────────────────
def page_detail():
    options = (df["cb_id"] + " " + df["cb_name"]).tolist()
    current = st.session_state.get("cb")
    idx = next((i for i, o in enumerate(options) if o.split()[0] == current), 0)
    choice = st.selectbox("選擇可轉債", options, index=idx)
    cb_id = choice.split()[0]
    st.session_state.cb = cb_id
    r = df[df["cb_id"] == cb_id].iloc[0]

    st.header(f"{r['cb_name']}（{cb_id}）")
    st.caption(f"對應股票 {r['stock_id']}　·　資料日期 {day}")
    if r.get("is_exchangeable"):
        st.warning("這是交換債，換的是別家公司的股票，溢價率不適用。")
    if r.get("conv_not_started"):
        st.info("轉換期間還沒開始，目前不能轉換成股票。")
    if r.get("low_liquidity"):
        st.warning(f"今天只成交 {num(r.get('volume'), '{:,.0f}')} 張，收盤價參考性低，溢價率請保守看待。")
    if r.get("issue_price_check") == "不一致":
        st.error("公開資訊觀測站與櫃買中心的發行時轉換價不一致，這檔的轉換價可能有誤，請先人工確認。")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("可轉債價", num(r.get("cb_close")))
    c2.metric("股價", num(r.get("stock_close")))
    c3.metric("轉換價", num(r.get("conv_price_now")))
    c4.metric("轉換價值", num(r.get("conv_value")))
    c5.metric("溢價率", pct(r.get("premium"), 2))

    tab_trend, tab_terms, tab_score, tab_src = st.tabs(["走勢", "條件", "研究評分", "資料來源"])
    with tab_trend:
        hist = load_history()
        h = hist[hist["cb_id"] == cb_id].sort_values("date").set_index("date")
        if len(h) < 2:
            st.info("歷史資料還不到兩天，累積幾天後就會出現走勢圖。")
        else:
            line_chart(h[["cb_close", "conv_value"]].rename(columns={"cb_close": "可轉債價", "conv_value": "轉換價值"}),
                       "可轉債價與轉換價值（每 100 元面額）")
            line_chart((h["premium"] * 100).rename("溢價率（%）").to_frame(), "溢價率（%）")
    with tab_terms:
        terms = {
            "發行時轉換價": num(r.get("conv_price")),
            "目前轉換價": f"{num(r.get('conv_price_now'))}（{r.get('conv_price_source', '—')}）",
            "轉換價生效日": r.get("conv_price_effective") if pd.notna(r.get("conv_price_effective")) else "—",
            "轉換期間": f"{r.get('conv_start', '—')} ～ {r.get('conv_end', '—')}",
            "賣回日／賣回價": f"{r.get('put_date', '—')}／{num(r.get('put_price'))}",
            "到期日": r.get("maturity", "—"),
            "目前餘額": num(r.get("balance"), "{:,.0f}"),
            "法人 5 日買超（張）": num(r.get("inst_net_5d"), "{:,.0f}"),
        }
        st.table(pd.DataFrame(terms.items(), columns=["項目", "內容"]).astype(str).set_index("項目"))
    with tab_score:
        st.info("研究評分是 P1 的開發項目，完成後會在這裡顯示：")
        st.markdown("- **發行公司體質**：負債比、流動比率、利息保障倍數、財務危機分數\n"
                    "- **可轉債評價**：純債價值、賣回收益率、與市價比較\n"
                    "- **稀釋效果**：全部轉換後股本增加比例\n"
                    "- **綜合評分與理由**：每個分數都附計算依據")
    with tab_src:
        src = {
            "股價日期": r.get("stock_date", "—"),
            "可轉債價格日期": r.get("cb_price_date") if pd.notna(r.get("cb_price_date")) else "當天沒有成交",
            "轉換價來源": r.get("conv_price_source", "—"),
            "代號對應": "⚠ 發行公司代號和可轉債代號不同" if r.get("code_mismatch") else "一致",
            "發行時轉換價核對（MOPS 與櫃買中心）": r.get("issue_price_check", "—"),
            "當天成交量（張）": num(r.get("volume"), "{:,.0f}"),
        }
        st.table(pd.DataFrame(src.items(), columns=["項目", "內容"]).astype(str).set_index("項目"))
        st.caption("公告連結（P0 待做）：之後會連到公開資訊觀測站的相關公告。")


# ── 資料品質 ────────────────────────────────────────────
def page_quality():
    st.header("資料品質")
    if quality.empty:
        st.info("還沒有品質報告。執行新版 build.py 後就會產生 data/quality_report.csv。")
        return
    q = quality.sort_values("date")
    c1, c2, c3 = st.columns(3)
    c1.metric("紀錄天數", f"{len(q)} 天")
    c2.metric("正常", f"{(q['status'] == 'OK').sum()} 天")
    c3.metric("有警告", f"{(q['status'] == 'WARN').sum()} 天")
    latest = q.iloc[-1]
    if pd.notna(latest.get("mops_age_days")) and str(latest.get("mops_age_days")) != "":
        st.caption(f"最近一次 MOPS 轉換價更新：{int(float(latest['mops_age_days']))} 天前（{latest['date']} 時）")

    flog = load_fetch_log()
    if not flog.empty:
        failed = flog[flog["status"] == "FAILED"]
        recent = failed.tail(5).iloc[::-1]
        if len(recent):
            with st.expander(f"抓取失敗紀錄（共 {len(failed)} 次，顯示最近 5 次）"):
                st.dataframe(recent.rename(columns={"run_at": "執行時間", "day": "資料日期", "status": "結果",
                                                    "message": "原因"}), hide_index=True, width="stretch")
                st.caption("排程失敗時會在 19:30 再試一次；仍缺的日期可以用 backfill.py 補抓。")

    trading = q[q["status"] != "CLOSED"].set_index("date")
    if len(trading) >= 2:
        line_chart((trading[["traded_ratio", "stock_coverage"]].astype(float) * 100).rename(
            columns={"traded_ratio": "有成交（%）", "stock_coverage": "有股價（%）"}), "有成交比例、有股價比例（%）", ",.1f")
    names = {"date": "日期", "status": "狀態", "cb_count": "檔數", "traded_count": "有成交",
             "missing_conv_price": "缺轉換價", "deep_discount": "折價 > 5%", "stale_stock_date": "股價日期不符",
             "issue_price_mismatch": "MOPS 核對不一致", "low_liquidity": "成交少於 10 張",
             "mops_age_days": "MOPS 幾天前更新", "count_change": "檔數變動",
             "unexplained_conv_change": "轉換價無紀錄變動", "price_jumps": "價格單日大變動",
             "mops_adjusted": "MOPS 轉換價", "foreign_excluded": "排除外幣債", "expired_excluded": "排除到期",
             "code_mismatch": "代號不一致", "warnings": "警告"}
    st.dataframe(q.sort_values("date", ascending=False)[[c for c in names if c in q]].rename(columns=names),
                 hide_index=True, width="stretch")


# ── 方法說明 ────────────────────────────────────────────
def page_method():
    st.header("方法說明")
    st.markdown("""
**計算方式**

- 轉換價值 ＝ 100 ÷ 目前轉換價 × 股價
- 溢價率 ＝ 可轉債收盤價 ÷ 轉換價值 − 1（負數代表折價）
- 目前轉換價：公開資訊觀測站有已生效的調整紀錄就用最新一筆，否則用發行時轉換價

**資料來源**

| 資料 | 來源 |
| --- | --- |
| 可轉債發行條件 | 櫃買中心 OpenAPI |
| 可轉債每日收盤價 | 櫃買中心每日行情檔（只取等價交易） |
| 上市股價 | 證交所每日收盤行情 |
| 上櫃股價 | 櫃買中心 OpenAPI |
| 三大法人 | 證交所、櫃買中心三大法人日報 |
| 最新轉換價 | 公開資訊觀測站（每週更新） |

**範圍與排除**

只含新台幣計價的上市櫃可轉債；排除私募債、外幣債、已到期與餘額為 0 的可轉債。
交換債保留在清單中，但不計算溢價率。

**聲明**

本系統為學生研究專案，所有資料以收盤後公開資料計算，可能有延遲或錯誤。
內容僅供研究，不構成任何投資建議。實際交易還需考慮手續費、交易稅、流動性與放空限制。
""")


{"首頁": page_home, "新手專區": page_beginner, "可轉債清單": page_list, "指標分析": page_metrics,
 "個股頁": page_detail,
 "資料品質": page_quality, "方法說明": page_method}[st.session_state.page]()