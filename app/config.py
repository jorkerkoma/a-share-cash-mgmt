import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
WEB_DIR = BASE_DIR / "web"
DB_PATH = DATA_DIR / "cash_mgmt.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn/"

# 默认抓取关键词（覆盖现金管理、委托理财、购买理财产品、结构性存款，按公告ID自动去重）
DEFAULT_KEYWORDS = ["现金管理", "委托理财", "购买理财产品", "结构性存款"]

# 默认抓取近92天（近3个月）
DEFAULT_LOOKBACK_DAYS = 92

# 并发配置（内存流式解析，不落盘）
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "8"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
USE_DIRECT_CNINFO = os.getenv("USE_DIRECT_CNINFO", "auto")  # auto / true / false

# 可选：大模型兜底配置（默认留空即禁用，纯本地免费运行）
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.minimaxi.com/v1").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M2.5").strip()
LLM_ENABLED = bool(LLM_API_KEY) and os.getenv("LLM_ENABLED", "false").lower() in ("1", "true", "yes")
