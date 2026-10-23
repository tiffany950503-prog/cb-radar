"""MOPS 轉換價調整：從公開資訊觀測站「最近三個月現況查詢」一次抓所有可轉債的最新轉換價，
把有調整過的寫進 data/conv_price_adjustments.csv，build.py 會自動套用。

查詢參數照網站頁面（t120sb02_q10）實際送出的表單設定，2026-10-22 在瀏覽器確認：
一次請求回傳約 425 列 × 113 欄，涵蓋所有轉換公司債。

注意：公開資訊觀測站的 robots.txt 不允許自動程式抓取 /mops/。
這支程式每次只送「一個」請求，請只在自己電腦手動執行，不要放進 GitHub Actions；
轉換價通常一個月才變一次，每週跑一次就夠了。

用法：
  python mops_conv_price.py           # 更新 conv_price_adjustments.csv
  python mops_conv_price.py --probe   # 只下載並印出幾檔做檢查，不寫檔
"""
import datetime as dt
import io
import re
import sys

import pandas as pd
import requests

import config as C

MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t120sb02"
HEADERS = {"User-Agent": "ntpu-gdg-cb-project (student project, manual run)"}

# 和網站查詢頁的表單預設值一致；少送任何一個欄位，網站可能只回傳一家公司
_EMPTY = ["filename", "mtypek", "bond_id_1", "bond_id_2", "issuer",
          "issue_date_yy_1", "issue_date_mm_1", "issue_date_dd_1",
          "issue_date_yy_2", "issue_date_mm_2", "issue_date_dd_2",
          "mature_date_yy_1", "mature_date_mm_1", "mature_date_dd_1",
          "mature_date_yy_2", "mature_date_mm_2", "mature_date_dd_2",
          "underwriter", "bd_currency", "issueFlag", "coupon_kind", "coupon_rate_1", "coupon_rate_2",
          "spec_issue", "guaranteed_0", "eva_kind", "eva_unit", "evarank", "guaranteed_1",
          "listornot", "maturity_hmyear_1", "maturity_hmyear_2", "raiseway", "green_bond"]
FORM = {
    **{k: "" for k in _EMPTY},
    "encodeURIComponent": "1",
    "firstin": "true",
    "TYPEK": "",
    "step": "12",            # 12 = 整包資料（113 欄），11 = 網頁清單（13 欄）
    "bond_kind": "5,7",      # 轉換公司債
    "nh": "n",               # 最近三個月現況
    "issuer_stock_code_1": "1000",
    "issuer_stock_code_2": "9999",
    "ontime": "Y",
    "overtime": "0",
}

# 欄位位置（從 1 開始數），2026-10-22 用實際資料確認
COL_CB_ID = 4             # 債券代碼
COL_CB_NAME = 5           # 債券簡稱
COL_ORIGINAL_PRICE = 43   # 發行時認股價格（發行時轉換價）
COL_CURRENT_PRICE = 44    # 最新認股價格（最新轉換價）
COL_EFFECTIVE_DATE = 45   # 最新認股價格生效日期

ADJ_PATH = C.DATA_DIR / "conv_price_adjustments.csv"
RAW_DIR = C.RAW_DIR / "mops"


def download():
    r = requests.post(MOPS_URL, data=FORM, timeout=120, headers=HEADERS)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"t120sb02_{dt.date.today():%Y%m%d}.html"
    path.write_text(r.text, encoding="utf-8")
    print(f"已下載：{path.name}（{len(r.text) / 1e6:.1f} MB）")
    return r.text


def _clean(t):
    """表頭跑到第一列時拉回表頭，並去掉重複的表頭列。"""
    t = t.astype(str).apply(lambda s: s.str.strip())
    if len(t) and (t.iloc[0] == "債券代碼").any():
        t = t.iloc[1:]
    return t[~(t == "債券代碼").any(axis=1)]


def parse(html):
    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")
    except ValueError:
        tables = []
    tables = [t for t in tables if t.shape[1] >= COL_EFFECTIVE_DATE]
    if not tables:
        raise SystemExit("回傳的網頁裡沒有可轉債資料表。請打開 data/raw/mops 裡最新的 html 看內容，或截圖給 Claude。")
    width = max(t.shape[1] for t in tables)
    parts = [_clean(t) for t in tables if t.shape[1] == width]
    for p in parts:
        p.columns = range(width)
    return pd.concat(parts, ignore_index=True)


def to_date(s):
    """2026/10/21、20261021、115/10/21 都轉成 2026-10-21。"""
    m = re.fullmatch(r"(\d{2,4})[/\-.]?(\d{1,2})[/\-.]?(\d{1,2})", str(s).strip())
    if not m:
        return None
    y, mth, d = (int(x) for x in m.groups())
    y += 1911 if y < 1911 else 0
    try:
        return dt.date(y, mth, d).isoformat()
    except ValueError:
        return None


def extract(df):
    num = lambda s: pd.to_numeric(s.str.replace(",", ""), errors="coerce")
    out = pd.DataFrame({
        "cb_id": df[COL_CB_ID - 1],
        "cb_name": df[COL_CB_NAME - 1],
        "original": num(df[COL_ORIGINAL_PRICE - 1]),
        "conv_price": num(df[COL_CURRENT_PRICE - 1]),
        "effective_date": df[COL_EFFECTIVE_DATE - 1].map(to_date),
    })
    out = out[out["cb_id"].str.fullmatch(r"\d{5,6}") & out["conv_price"].gt(0)]
    out = out.drop_duplicates("cb_id", keep="last")
    changed = out[(out["conv_price"] - out["original"]).abs() > 1e-6].copy()
    changed["effective_date"] = changed["effective_date"].fillna(dt.date.today().isoformat())
    return out, changed


def merge_into_file(new):
    """和現有檔案合併：同一檔、同一生效日只留一筆，手動加的紀錄也會保留。

    recorded_at：這筆調整是「第一次」被我們抓到的日期，給 build.py 的 point-in-time
    過濾用（select_known_adjustments）。只有真的沒看過的 (cb_id, effective_date) 組合
    才蓋上今天的日期；已經在檔案裡的，保留它原本的 recorded_at（即使是空的），
    不能因為重新抓到同一筆就把「第一次知道」的時間往後推。
    """
    cols = ["cb_id", "conv_price", "effective_date"]
    if ADJ_PATH.exists():
        old = pd.read_csv(ADJ_PATH, dtype=str, encoding="utf-8-sig", skipinitialspace=True)
        old.columns = old.columns.str.strip().str.lstrip("﻿")
    else:
        old = pd.DataFrame(columns=cols)
    if "recorded_at" not in old.columns:
        old["recorded_at"] = pd.NA

    today = dt.date.today().isoformat()
    existing_keys = set(zip(old["cb_id"].astype(str), old["effective_date"].astype(str)))
    new = new.assign(conv_price=new["conv_price"].map(lambda x: f"{x:g}"))
    new["recorded_at"] = [
        pd.NA if (cb, eff) in existing_keys else today
        for cb, eff in zip(new["cb_id"].astype(str), new["effective_date"].astype(str))
    ]

    allcols = cols + ["recorded_at"]
    allrows = pd.concat([old[allcols].astype(str), new[allcols].astype(str)], ignore_index=True)
    allrows["cb_id"] = allrows["cb_id"].str.strip()
    allrows.loc[allrows["recorded_at"].isin(["nan", "<NA>", "None"]), "recorded_at"] = ""
    # old 在前、new 在後；keep="first" 保留舊紀錄原本的 recorded_at，不被重新抓到的這次覆蓋
    allrows = (allrows.drop_duplicates(["cb_id", "effective_date"], keep="first")
                      .sort_values(["cb_id", "effective_date"]))
    allrows.to_csv(ADJ_PATH, index=False, encoding="utf-8-sig")
    return len(old), len(allrows)


def main():
    allcb, changed = extract(parse(download()))
    print(f"共 {len(allcb)} 檔可轉債，其中 {len(changed)} 檔轉換價調整過。")
    if len(allcb) < 300:
        print("⚠ 檔數比平常（約 400 檔）少很多，查詢可能沒有完整回傳，請截圖給 Claude。")
    for _, r in changed.head(5).iterrows():
        print(f"  {r['cb_id']} {r['cb_name'][:8]}：{r['original']:g} → {r['conv_price']:g}（生效 {r['effective_date']}）")
    if "--probe" in sys.argv:
        print("（--probe 模式，沒有寫檔）")
        return
    before, after = merge_into_file(changed[["cb_id", "conv_price", "effective_date"]])
    print(f"conv_price_adjustments.csv：{before} 筆 → {after} 筆")
    print("接著執行 python build.py <日期> 就會套用新的轉換價。")


if __name__ == "__main__":
    main()