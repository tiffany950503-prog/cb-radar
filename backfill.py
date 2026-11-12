"""補抓缺少的交易日：一天一天抓資料、建表。已經有結果表的日期會跳過。

用法：
  python backfill.py 2026-10-23 2026-11-05     # 補這段期間缺的日子
  python backfill.py 2026-10-23 2026-11-05 --dry   # 只列出會補哪幾天，不真的抓

注意：
- 要照日期從舊到新補，「跟前一天比對」才會正確，程式會自動照順序。
- 可轉債發行條件只有「目前」的版本，過去的日子會用今天的條件計算；
  已到期、當時還沒發行的會依日期排除，轉換價會用當天已生效的版本。
- 每天之間會停一下，避免對網站送太多請求。
"""
import datetime as dt
import sys
import time

import build
import config as C
import fetch


def trading_days(start, end):
    d = dt.date.fromisoformat(start)
    while d <= dt.date.fromisoformat(end):
        if d.weekday() < 5:
            yield d.isoformat()
        d += dt.timedelta(days=1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    todo = [d for d in trading_days(*args) if not (C.DATA_DIR / f"daily_{d}.csv").exists()]
    print(f"要補的日子：{'、'.join(todo) if todo else '沒有，全部都有資料了'}")
    if "--dry" in sys.argv or not todo:
        return
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
        print(f"失敗 {len(failed)} 天：{'、'.join(failed)}（可以晚一點再跑一次，只會補還缺的）")


if __name__ == "__main__":
    main()