"""共用設定。欄位對照表要在第一次跑完 discover 之後，依實際欄位名稱修改。"""
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "cb.db"

TPEX_SWAGGER = "https://www.tpex.org.tw/openapi/swagger.json"
TPEX_BASE = "https://www.tpex.org.tw/openapi"
TWSE_BASE = "https://openapi.twse.com.tw/v1"

# 搜尋可轉債相關端點時用的關鍵字
CB_KEYWORDS = ["轉換", "轉(交)換", "可轉", "convertible", "cb"]

# ── 端點 ───────────────────────────────────────────────
CB_TERMS_PATH = "/v1/bond_ISSBD5_data"  # 可轉債發行條件（櫃買中心 OpenAPI）

# 可轉債每日行情：OpenAPI 沒有，改抓櫃買中心每日行情 CSV（Big5 編碼）
CB_QUOTES_URL = ("https://www.tpex.org.tw/storage/bond_zone/tradeinfo/cb/"
                 "{y}/{ym}/RSta0113.{ymd}-C.csv")

# 全市場股票收盤價（一次抓一整個市場，省 API 次數）
TWSE_STOCK_PATH = "/exchangeReport/STOCK_DAY_ALL"          # 上市（只有最新一天，已改用下面的網址）
TWSE_DAILY_URL = ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
                  "?date={ymd}&type=ALLBUT0999&response=json")  # 上市，可指定日期
TPEX_STOCK_PATH = "/v1/tpex_mainboard_daily_close_quotes"  # 上櫃（只有最新一天、常斷線，已不用）
TPEX_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"  # 上櫃，可指定日期（POST）

# ── 欄位對照：左邊是我們程式用的名字，右邊是 API 實際欄位 ──────
# 第一次跑 fetch 後打開 data/raw/*.json 看欄位，再改右邊的值
TERMS_FIELDS = {
    "cb_id": "BondCode",
    "cb_name": "ShortName",
    "conv_price": "Conversion/ExchangePriceAtIssuance",
    "conv_start": "Conversion/ExchangePeriodStartDate",
    "conv_end": "Conversion/ExchangePeriodEndDate",
    "put_date": "PutOptionDate",
    "put_price": "PutOptionPrice",
    "maturity": "MaturityDate",
    "balance": "OutstandingAmount",
    "currency": "Currency",
    "issuer_code": "IssuerCode",
    "issue_date": "IssueDate",
}
# 行情 CSV 由 fetch.py 自己解析成固定欄位，不用改
QUOTE_FIELDS = {"cb_id": "cb_id", "close": "close", "volume": "volume"}
TWSE_STOCK_FIELDS = {"stock_id": "Code", "close": "ClosingPrice"}
TPEX_STOCK_FIELDS = {"stock_id": "SecuritiesCompanyCode", "close": "Close"}

# 三大法人日報（官方，一次全市場）。2026-10-22 用瀏覽器確認格式
TWSE_INST_URL = ("https://www.twse.com.tw/rwd/zh/fund/T86"
                 "?date={ymd}&selectType=ALLBUT0999&response=json")  # 上市，最後一欄「三大法人買賣超股數」
TPEX_INST_URL = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
                 "?type=Daily&sect=EW&date={ymd}&response=json")  # 上櫃，日期格式 2026/10/21
INST_DAYS = 5  # 算幾個交易日的累計買賣超