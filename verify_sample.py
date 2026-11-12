"""資料核對工具（文件 2.2：先以 30～50 檔做資料核對）。

步驟 1：產生核對表
  python verify_sample.py make 2026-10-30          # 預設抽 40 檔
  python verify_sample.py make 2026-10-30 --n 50
  → data/verify/verify_<日期>.csv，用 Excel 打開，到券商 App 或櫃買中心網站查，
    把「外部_」開頭的欄位填好、存檔（存成 CSV）。

步驟 2：計算一致率
  python verify_sample.py check 2026-10-30
  → 印出每一項的一致率與不一致清單，並存成 data/verify/result_<日期>.csv

抽樣方式：所有「折價且有成交」的必抽（這些最常被用到，錯了影響最大），
其餘從有成交的可轉債隨機抽；同一天每次抽到的一樣，方便分工。
"""
import sys

import pandas as pd

import config as C

VERIFY_DIR = C.DATA_DIR / "verify"
# 容許誤差：價格差 0.05 元以內、溢價率差 0.3 個百分點以內，視為一致
TOL_PRICE = 0.05
TOL_PREMIUM_PT = 0.3

CHECKS = [  # (我們的欄位, 外部欄位, 容許誤差, 顯示名稱)
    ("cb_close", "外部_可轉債收盤價", TOL_PRICE, "可轉債收盤價"),
    ("stock_close", "外部_股票收盤價", TOL_PRICE, "股票收盤價"),
    ("conv_price_now", "外部_轉換價", 0.01, "轉換價"),
    ("premium_pct", "外部_溢價率（%）", TOL_PREMIUM_PT, "溢價率"),
]


def load_daily(day):
    path = C.DATA_DIR / f"daily_{day}.csv"
    if not path.exists():
        raise SystemExit(f"找不到 {path.name}，請先執行 build.py {day}。")
    df = pd.read_csv(path, dtype={"cb_id": str, "stock_id": str}, encoding="utf-8-sig")
    df["traded_today"] = df["traded_today"].astype(str).str.lower().isin(["true", "1"])
    return df


def make(day, n):
    df = load_daily(day)
    traded = df[df["traded_today"] & df["premium"].notna()]
    must = traded[traded["premium"] < 0]
    rest = traded.drop(must.index)
    seed = int(day.replace("-", ""))
    extra = rest.sample(n=max(0, min(n - len(must), len(rest))), random_state=seed)
    sample = pd.concat([must, extra]).sort_values("cb_id")
    sheet = pd.DataFrame({
        "cb_id": sample["cb_id"], "名稱": sample["cb_name"].str.strip(), "股票代號": sample["stock_id"],
        "cb_close": sample["cb_close"], "stock_close": sample["stock_close"],
        "conv_price_now": sample["conv_price_now"], "premium_pct": (sample["premium"] * 100).round(2),
        "外部_可轉債收盤價": "", "外部_股票收盤價": "", "外部_轉換價": "", "外部_溢價率（%）": "",
        "查詢來源": "", "核對人": "", "備註": "",
    })
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    path = VERIFY_DIR / f"verify_{day}.csv"
    if path.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{path.name} 已經存在（可能已經填了一部分）。要重新產生請加 --force。")
    sheet.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"已產生 {path}：共 {len(sheet)} 檔（其中折價必抽 {len(must)} 檔）。")
    print("用 Excel 打開，填好「外部_」開頭的欄位後存檔（格式維持 CSV），再執行：")
    print(f"  python verify_sample.py check {day}")


def check(day):
    path = VERIFY_DIR / f"verify_{day}.csv"
    if not path.exists():
        raise SystemExit(f"找不到 {path.name}，請先執行 python verify_sample.py make {day}")
    try:
        sheet = pd.read_csv(path, dtype={"cb_id": str}, encoding="utf-8-sig")
    except UnicodeDecodeError:  # Excel 有時會存成 Big5
        sheet = pd.read_csv(path, dtype={"cb_id": str}, encoding="cp950")
    rows, problems = [], []
    print(f"{day} 資料核對結果（{len(sheet)} 檔）：")
    for ours, theirs, tol, label in CHECKS:
        a = pd.to_numeric(sheet[ours], errors="coerce")
        b = pd.to_numeric(sheet[theirs].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce")
        filled = b.notna()
        ok = filled & ((a - b).abs() <= tol)
        n = int(filled.sum())
        rate = ok.sum() / n if n else float("nan")
        rows.append({"項目": label, "已填檔數": n, "一致": int(ok.sum()),
                     "一致率": round(rate, 4) if n else "", "容許誤差": tol})
        mark = "—" if not n else ("✓" if rate == 1 else "⚠")
        print(f"  {mark} {label}：已填 {n} 檔，一致 {int(ok.sum())} 檔" + (f"（{rate:.1%}）" if n else ""))
        for i in sheet.index[filled & ~ok]:
            problems.append(f"{sheet.at[i, 'cb_id']} {sheet.at[i, '名稱']}｜{label}：我們 {a[i]:g}，外部 {b[i]:g}")
    if problems:
        print("\n不一致清單（請查原因：轉換價沒更新？抓錯日期？對錯股票？）")
        for p in problems:
            print(f"  - {p}")
    out = VERIFY_DIR / f"result_{day}.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n結果已存到 {out}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2 or args[0] not in ("make", "check"):
        raise SystemExit(__doc__)
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 40
    (make if args[0] == "make" else check)(args[1], *([n] if args[0] == "make" else []))


if __name__ == "__main__":
    main()