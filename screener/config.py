import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DATASET_PATH = DATA_DIR / "screener.parquet"
JP_PATH = DATA_DIR / "jp.parquet"
HISTORY_US_PATH = DATA_DIR / "history_us.parquet"
HISTORY_JP_PATH = DATA_DIR / "history_jp.parquet"

# SEC はアクセス元の名前と連絡先を User-Agent に入れることを求めている
# 例: $env:SEC_USER_AGENT = "Taro Yamada taro@example.com"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "us-screener personal-research contact@example.com")
