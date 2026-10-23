"""calc_valuation() 的公式測試：轉換價值/溢價率、null 理由代碼、訊號分級。

企劃書驗收標準：至少 20 個公式測試案例、50 檔處理時間在 10 分鐘內、無重複警示。

用法：pytest test_build.py -v
"""
from decimal import Decimal
import time

import pandas as pd
import pytest

from build import MIN_VOLUME, calc_valuation, select_known_adjustments

DAY = "2026-10-22"
STARTED = "20200101"      # 早就開始的轉換期間
NOT_STARTED = "20270101"  # 還沒開始的轉換期間


def row(cb_close=100.0, stock_close=100.0, conv_price_now=100.0, volume=100,
        conv_start=STARTED, cb_name="測試一", cb_id="11111", stock_id="1111"):
    """建一筆最小輸入列，預設是「剛好打平、可轉換、有成交、量夠」的正常案例。"""
    return pd.DataFrame([{
        "cb_id": cb_id, "cb_name": cb_name, "stock_id": stock_id,
        "cb_close": cb_close, "stock_close": stock_close, "volume": volume,
        "conv_price_now": conv_price_now, "conv_start": conv_start, "date": DAY,
    }])


def calc1(**kwargs):
    return calc_valuation(row(**kwargs)).iloc[0]


# ── 正常計算：折價／溢價／打平 ──────────────────────────────
@pytest.mark.parametrize("cb_close, stock_close, conv_price_now, expect_premium", [
    (90, 100, 100, -0.10),   # 折價 10%
    (110, 100, 100, 0.10),   # 溢價 10%
    (100, 100, 100, 0.0),    # 剛好打平
    (95, 200, 100, -0.525),  # 轉換價值 200，CB 95 → 折價 52.5%
    (150, 80, 100, 0.875),   # 溢價 87.5%
])
def test_premium_formula(cb_close, stock_close, conv_price_now, expect_premium):
    r = calc1(cb_close=cb_close, stock_close=stock_close, conv_price_now=conv_price_now)
    assert r["premium"] == pytest.approx(expect_premium)
    assert r["premium_null_reason"] is None


# ── null 理由代碼：四種情況都要有明確理由，不能是裸 NaN ──────
def test_null_reason_not_traded():
    r = calc1(cb_close=None)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "not_traded"


def test_null_reason_no_stock_price():
    r = calc1(stock_close=None)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "no_stock_price"


@pytest.mark.parametrize("conv_price_now", [None, 0, -5])
def test_null_reason_no_conv_price(conv_price_now):
    r = calc1(conv_price_now=conv_price_now)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "no_conv_price"


@pytest.mark.parametrize("cb_name", ["測試E", "某交換債E", "ABC-E"])
def test_null_reason_exchangeable(cb_name):
    r = calc1(cb_name=cb_name, cb_close=90)  # 即使有成交、折價，交換債也要清空 premium
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "exchangeable"
    assert r["is_exchangeable"]


def test_null_reason_priority_exchangeable_over_not_traded():
    """交換債且沒成交：理由要是 exchangeable，不是 not_traded（exchangeable 優先判斷）。"""
    r = calc1(cb_name="測試E", cb_close=None)
    assert r["premium_null_reason"] == "exchangeable"


# ── 訊號分級：candidate／suspect／none，跟「能不能直接拿來判斷套利」分開 ──
def test_signal_candidate_mild_discount():
    r = calc1(cb_close=98, stock_close=100, conv_price_now=100)  # premium ≈ -0.02
    assert r["signal"] == "candidate"


def test_signal_suspect_deep_discount():
    r = calc1(cb_close=80, stock_close=100, conv_price_now=100)  # premium = -0.20，超過門檻
    assert r["signal"] == "suspect"


def test_signal_just_above_threshold_is_candidate():
    """門檻是 -5%；折價 4.9% 還在門檻之內，算 candidate。"""
    r = calc1(cb_close=95.1, stock_close=100, conv_price_now=100)
    assert r["signal"] == "candidate"


def test_signal_just_below_threshold_is_suspect():
    """折價 5.1%，超過門檻，算 suspect（可疑，不是機會）。"""
    r = calc1(cb_close=94.9, stock_close=100, conv_price_now=100)
    assert r["signal"] == "suspect"


def test_signal_none_when_premium_positive():
    r = calc1(cb_close=120, stock_close=100, conv_price_now=100)
    assert r["signal"] == "none"


def test_signal_none_when_exchangeable():
    r = calc1(cb_name="測試E", cb_close=80, stock_close=100, conv_price_now=100)
    assert r["signal"] == "none"


def test_signal_none_when_not_started():
    r = calc1(cb_close=90, conv_start=NOT_STARTED)
    assert r["conv_not_started"]
    assert r["signal"] == "none"
    # 還沒能轉換，但 premium 仍照算（之後能轉換時才有意義），不是 null
    assert r["premium"] == pytest.approx(-0.10)


def test_signal_none_when_not_traded():
    r = calc1(cb_close=None)
    assert r["signal"] == "none"


# ── 效能與無重複警示（企劃書驗收數字） ────────────────────────
def test_performance_500_rows_under_10_minutes():
    n = 500
    df = pd.concat([row(cb_id=str(10000 + i), stock_id=str(1000 + i),
                         cb_close=90 + (i % 20), conv_start=STARTED)
                     for i in range(n)], ignore_index=True)
    start = time.perf_counter()
    out = calc_valuation(df)
    elapsed = time.perf_counter() - start
    assert len(out) == n
    assert elapsed < 600  # 企劃書驗收數字：50 檔 10 分鐘內；500 檔也留足餘裕


def test_no_duplicate_signal_per_cb_per_day():
    df = pd.concat([row(cb_id="11111"), row(cb_id="22222")], ignore_index=True)
    out = calc_valuation(df)
    assert not out["cb_id"].duplicated().any()


# ── 流動性門檻：量太薄不當訊號，即使折價 ──────────────────────
def test_signal_none_when_volume_too_thin():
    r = calc1(cb_close=90, volume=MIN_VOLUME - 1)
    assert r["signal"] == "none"
    assert r["premium"] == pytest.approx(-0.10)  # premium 照算，只是不當訊號


def test_signal_candidate_when_volume_enough():
    r = calc1(cb_close=98, volume=MIN_VOLUME)  # 剛好等於門檻，算夠
    assert r["signal"] == "candidate"


def test_signal_suspect_still_needs_volume():
    r = calc1(cb_close=80, volume=0)  # 深度折價但量是 0
    assert r["signal"] == "none"


# ── Point-in-time：select_known_adjustments 只留「當時已經知道」的調整 ──
def adj_row(cb_id, conv_price, effective_date, recorded_at=None):
    return {"cb_id": cb_id, "conv_price": conv_price, "effective_date": effective_date,
            "recorded_at": recorded_at}


def test_known_adjustments_excludes_future_effective_date():
    adj = pd.DataFrame([adj_row("11111", "90", "2027-01-01", "2026-01-01")])
    known = select_known_adjustments(adj, "2026-06-01")
    assert known.empty


def test_known_adjustments_excludes_not_yet_recorded():
    """生效日在過去，但我們是後來才抓到的（公告有延遲）：build 那天還不知道，不能用。"""
    adj = pd.DataFrame([adj_row("11111", "90", "2026-01-01", "2026-09-01")])
    known = select_known_adjustments(adj, "2026-06-01")
    assert known.empty


def test_known_adjustments_includes_when_both_in_past():
    adj = pd.DataFrame([adj_row("11111", "90", "2026-01-01", "2026-01-15")])
    known = select_known_adjustments(adj, "2026-06-01")
    assert len(known) == 1
    assert known.iloc[0]["conv_price"] == "90"


def test_known_adjustments_missing_recorded_at_falls_back_to_effective_date():
    """沒記錄 recorded_at（舊資料）時，保守假設跟生效日同時已知。"""
    adj = pd.DataFrame([adj_row("11111", "90", "2026-01-01", None)])
    known = select_known_adjustments(adj, "2026-06-01")
    assert len(known) == 1


def test_known_adjustments_picks_latest_effective_date_per_cb():
    adj = pd.DataFrame([
        adj_row("11111", "90", "2026-01-01", "2026-01-01"),
        adj_row("11111", "85", "2026-03-01", "2026-03-01"),
    ])
    known = select_known_adjustments(adj, "2026-06-01")
    assert len(known) == 1
    assert known.iloc[0]["conv_price"] == "85"


# ── Decimal 精確運算：避開 float 二進位誤差 ─────────────────────
def test_decimal_avoids_float_binary_error():
    """0.1 這種數字二進位表示法本身就有誤差；用 Decimal(str(...)) 建構才能避開。"""
    r = calc1(cb_close=100.1, stock_close=100, conv_price_now=100)
    expected = (Decimal("100.1") / Decimal("100") - 1).quantize(Decimal("0.000001"))
    assert r["premium"] == pytest.approx(float(expected), abs=1e-12)


def test_decimal_result_is_quantized():
    """算出來的 premium 小數位數要落在量化精度內，不是未量化的浮點雜訊。"""
    r = calc1(cb_close=97, stock_close=133, conv_price_now=41)
    # 乘 1e6 後應該非常接近整數（量化到小數 6 位）
    assert abs(r["premium"] * 1e6 - round(r["premium"] * 1e6)) < 1e-6
