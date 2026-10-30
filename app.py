"""可轉債雷達：研究 App 第一版（Streamlit）。

在自己電腦執行：
  pip install -r requirements.txt
  streamlit run app.py

只讀 data/ 裡已經算好的結果，不重新計算任何數字。
"""
from pathlib import Path

import pandas as pd
import streamlit as st

import alerts

DATA = Path(__file__).parent / "data"

st.set_page_config(page_title="可轉債雷達", page_icon="📈", layout="wide")


# ── 讀資料 ──────────────────────────────────────────────
@st.cache_data(ttl=600)
def available_days():
    return sorted(f.stem.replace("daily_", "") for f in DATA.glob("daily_*.csv"))


@st.cache_data(ttl=600)
def load_day(day):
    df = pd.read_csv(DATA / f"daily_{day}.csv", dtype={"cb_id": str, "stock_id": str}, encoding="utf-8-sig")
    for col in ["traded_today", "conv_not_started", "is_exchangeable", "code_mismatch"]:
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
def load_quality():
    p = DATA / "quality_report.csv"
    return pd.read_csv(p, dtype={"date": str}, encoding="utf-8-sig") if p.exists() else pd.DataFrame()


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

PAGES = ["首頁", "新手專區", "可轉債清單", "個股頁", "資料品質", "方法說明"]
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
    show = view[[c for c in cols if c in view]].rename(columns=cols)
    show["溢價率（%）"] = show["溢價率（%）"] * 100
    st.caption(f"共 {len(show)} 檔，點表格任一列可選取，再按下方按鈕看個股頁。")
    event = st.dataframe(
        show, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={"溢價率（%）": st.column_config.NumberColumn(format="%.2f"),
                       "法人 5 日（張）": st.column_config.NumberColumn(format="%.0f")})
    rows = event.selection.rows if event and event.selection else []
    if rows:
        cb_id = show.iloc[rows[0]]["代號"]
        st.button(f"查看 {cb_id} {show.iloc[rows[0]]['名稱']} 的個股頁", on_click=go_to_detail, args=(cb_id,))


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
            st.caption("可轉債價與轉換價值（每 100 元面額）")
            st.line_chart(h[["cb_close", "conv_value"]].rename(columns={"cb_close": "可轉債價", "conv_value": "轉換價值"}))
            st.caption("溢價率（%）")
            st.line_chart((h["premium"] * 100).rename("溢價率"))
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
    trading = q[q["status"] != "CLOSED"].set_index("date")
    if len(trading) >= 2:
        st.caption("有成交比例、有股價比例")
        st.line_chart(trading[["traded_ratio", "stock_coverage"]].astype(float).rename(
            columns={"traded_ratio": "有成交", "stock_coverage": "有股價"}))
    names = {"date": "日期", "status": "狀態", "cb_count": "檔數", "traded_count": "有成交",
             "missing_conv_price": "缺轉換價", "deep_discount": "折價 > 5%", "stale_stock_date": "股價日期不符",
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


{"首頁": page_home, "新手專區": page_beginner, "可轉債清單": page_list, "個股頁": page_detail,
 "資料品質": page_quality, "方法說明": page_method}[st.session_state.page]()