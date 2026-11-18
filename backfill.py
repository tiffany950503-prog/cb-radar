"""補抓缺少的交易日：一天一天抓資料、建表。已經有結果表的日期會跳過。

用法：
  python backfill.py 2026-10-23 2026-11-05         # 補這段期間缺的日子
  python backfill.py 2026-10-23 2026-11-05 --dry   # 只列出會補哪幾天，不真的抓
  python backfill.py --auto                        # 自動補：最近 30 天內缺的交易日（排程用這個）
  python backfill.py --auto --dry                  # 只列出自動模式會補哪幾天

自動模式（--auto）：
- GitHub 的排程常常延遲好幾個小時、偶爾也會失敗，所以排程不再只抓「今天」，
  而是每次都檢查最近 30 天，把還沒有結果表的交易日補齊。
  某天失敗了，下一次排程會自動再試，不用手動補。
- 台灣時間 17:00 以前不抓今天（收盤資料還沒出齊）。
- 已經判定休市的日子（資料品質報告裡是 CLOSED）不會重抓。
- 一次最多補 8 天，避免超過 GitHub 的執行時間上限；剩下的下一次排程會接著補。

注意：
- 要照日期從舊到新補，「跟前一天比對」才會正確，程式會自動照順序。
- 可轉債發行條件只有「目前」的版本，過去的日子會用今天的條件計算；
  已到期、當時還沒發行的會依日期排除，轉換價會用當天已生效的版本。
- 每天之間會停一下，避免對網站送太多請求。
"""
import datetime as dt
import sys
import time

import pandas as pd

import build
import config as C
import fetch

AUTO_LOOKBACK_DAYS = 30   # 自動模式往回檢查幾天
AUTO_MAX_DAYS = 8         # 自動模式一次最多補幾天


def trading_days(start, end):
    d = dt.date.fromisoformat(start)
    while d <= dt.date.fromisoformat(end):
        if d.weekday() < 5:
            yield d.isoformat()
        d += dt.timedelta(days=1)


def known_closed():
    """資料品質報告裡已經判定休市的日子。"""
    path = C.DATA_DIR / "quality_report.csv"
    if not path.exists():
        return set()
    q = pd.read_csv(path, dtype={"date": str}, encoding="utf-8-sig")
    return set(q.loc[q["status"] == "CLOSED", "date"])


def missing_days(start, end):
    closed = known_closed()
    return [d for d in trading_days(start, end)
            if d not in closed and not (C.DATA_DIR / f"daily_{d}.csv").exists()]


def auto_range():
    """自動模式的日期範圍：最近 30 天，到今天或昨天（看台灣時間有沒有過 17:00）。"""
    now = C.tw_now()
    end = now.date() if now.hour >= C.DATA_READY_HOUR else now.date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=AUTO_LOOKBACK_DAYS)
    print(f"台灣時間 {now:%Y-%m-%d %H:%M}，檢查 {start} ～ {end} 有沒有缺資料")
    return start.isoformat(), end.isoformat()


def run_days(todo):
    done, closed, failed = [], [], []
    for i, day in enumerate(todo):
        if i:
            time.sleep(10)
        try:
            status = fetch.run(day)
            build.build(day)
            (closed if status == "CLOSED" else done).append(day)
        except Exception as e:
            print(f"  {day} 失敗：{e}")
            failed.append(day)
        print()
    print(f"完成 {len(done)} 天：{'、'.join(done) or '—'}")
    if closed:
        print(f"休市 {len(closed)} 天：{'、'.join(closed)}")
    if failed:
        print(f"失敗 {len(failed)} 天：{'、'.join(failed)}（下次排程或晚一點再跑一次，只會補還缺的）")
    return failed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a.strip()]
    auto = "--auto" in sys.argv
    if auto:
        rng = auto_range()
    elif len(args) == 2:
        rng = args
    elif len(args) == 1:
        rng = (args[0], args[0])
    else:
        raise SystemExit(__doc__)
    todo = missing_days(*rng)
    later = []
    if auto and len(todo) > AUTO_MAX_DAYS:
        todo, later = todo[:AUTO_MAX_DAYS], todo[AUTO_MAX_DAYS:]
    print(f"要補的日子：{'、'.join(todo) if todo else '沒有，全部都有資料了'}")
    if later:
        print(f"（還有 {len(later)} 天留給下一次：{'、'.join(later)}）")
    if "--dry" in sys.argv or not todo:
        return
    failed = run_days(todo)
    if failed:
        sys.exit(1)   # 讓 GitHub Actions 顯示紅色叉叉，失敗原因寫在 data/fetch_log.csv


if __name__ == "__main__":
    main()
