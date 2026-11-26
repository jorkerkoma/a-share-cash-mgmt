import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from app.config import (
    CNINFO_QUERY_URL,
    CNINFO_STATIC_BASE,
    DEFAULT_KEYWORDS,
    REQUEST_TIMEOUT,
    USE_DIRECT_CNINFO,
)
from app.normalizer import infer_market


# 明显不包含具体买卖明细的公告标题过滤词
SKIP_TITLE_KEYWORDS = (
    "核查意见",
    "独立意见",
    "法律意见书",
    "管理制度",
    "管理办法",
    "内控制度",
    "议事规则",
    "董事会决议",
    "监事会决议",
    "股东大会决议",
    "股东会决议",
    "三方监管协议",
    "四方监管协议",
    "专用结算账户",
    "募集资金专户",
    "注销",
    "销户",
    "开立",
    "问询函",
    "监管函",
    "关注函",
    "年度报告",
    "半年度报告",
    "季度报告",
    "专项报告",
    "鉴证报告",
    "审计报告",
    "上市公告书",
    "招股说明书",
    "发行公告",
    "减持",
    "增持",
    "回购股份",
    "担保",
    "关联交易",
    "对外投资",
    "业绩预告",
    "业绩快报",
    "权益分派",
    "独立财务顾问",
    "保荐机构",
    "持续督导",
    "提示性公告",
    "召开",
    "会议通知",
    "取消",
    "补充确认",
)

# 强正向词：若标题含这些词，即使出现“补充确认”也应保留检查
POSITIVE_TITLE_KEYWORDS = (
    "进展",
    "赎回",
    "到期",
    "收回",
    "兑付",
    "购买",
    "认购",
    "实施",
    "理财产品",
    "结构性存款",
    "大额存单",
    "收益凭证",
)

# 纯额度审议标题特征（若不含进展/赎回/购买等正向词则极可能是纯额度授权）
QUOTA_HINT_KEYWORDS = (
    "增加",
    "提高",
    "调整",
    "变更",
    "额度",
    "2026年度",
    "2027年度",
    "拟使用",
)


def create_cninfo_session(direct: Optional[bool] = None) -> requests.Session:
    s = requests.Session()
    if direct is None:
        if USE_DIRECT_CNINFO == "true":
            s.trust_env = False
        elif USE_DIRECT_CNINFO == "false":
            s.trust_env = True
        else:
            # auto：国内巨潮直连稳定且避免被本机海外代理拖慢
            s.trust_env = False
    else:
        s.trust_env = not direct

    adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=2)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "Accept": "application/json, text/plain, */*",
        }
    )
    return s


def classify_title(title: str) -> Tuple[bool, str, str]:
    """
    返回 (should_fetch_pdf, ann_category, fund_source_hint)
    """
    clean_t = title.replace("<em>", "").replace("</em>", "").strip()

    # 资金来源线索
    has_raise = any(k in clean_t for k in ("募集资金", "超募资金", "募资"))
    has_own = any(k in clean_t for k in ("自有资金", "自筹资金"))
    if has_raise and has_own:
        fund_hint = "募集+自有资金"
    elif "超募资金" in clean_t:
        fund_hint = "闲置超募资金"
    elif has_raise:
        fund_hint = "闲置募集资金"
    elif has_own:
        fund_hint = "闲置自有资金"
    else:
        fund_hint = "未注明"

    # 必须和理财/现金管理相关（防止搜到无关公告）
    if not any(
        k in clean_t
        for k in ("现金管理", "委托理财", "理财产品", "结构性存款", "大额存单", "收益凭证", "闲置资金")
    ):
        return (False, "非交易过滤", fund_hint)

    # 券商核查意见/制度/专户协议等直接过滤
    if any(bad in clean_t for bad in SKIP_TITLE_KEYWORDS):
        # 特殊放行：例如“关于使用闲置资金购买理财产品暨关联交易的进展公告”
        if not ("进展" in clean_t or ("赎回" in clean_t and "核查意见" not in clean_t and "决议" not in clean_t)):
            return (False, "非交易过滤", fund_hint)

    # 明显仅修改额度且不含进展/购买/赎回
    has_pos = any(k in clean_t for k in POSITIVE_TITLE_KEYWORDS)
    if not has_pos and any(q in clean_t for q in ("增加", "提高", "调整", "变更", "额度")):
        return (False, "额度授权", fund_hint)

    # 判定交易类别
    has_redeem = any(k in clean_t for k in ("赎回", "到期收回", "到期兑付", "到期归还"))
    has_continue = any(k in clean_t for k in ("继续", "新购", "购买", "认购", "并开展", "并进行"))
    if has_redeem and has_continue:
        category = "赎回并新购"
    elif has_redeem and "进展" not in clean_t:
        category = "到期赎回"
    elif has_pos:
        category = "交易进展"
    else:
        # 例如《关于使用部分闲置募集资金进行现金管理的公告》——可能是额度授权，也可能是具体购买，拉取PDF进一步判断
        category = "待识别"

    return (True, category, fund_hint)


def _date_chunks(date_from: str, date_to: str, chunk_days: int = 31) -> List[Tuple[str, str]]:
    d1 = datetime.strptime(date_from, "%Y-%m-%d").date()
    d2 = datetime.strptime(date_to, "%Y-%m-%d").date()
    chunks = []
    cur = d1
    while cur <= d2:
        nxt = min(d2, cur + timedelta(days=chunk_days - 1))
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)
    return chunks


def fetch_announcement_list(
    date_from: str,
    date_to: str,
    keywords: Optional[List[str]] = None,
    session: Optional[requests.Session] = None,
    progress_cb=None,
) -> List[Dict]:
    """
    按日期分片 + 多关键词拉取全市场（沪深北）A股公告列表并按 announcementId 去重。
    巨潮单次检索分页上限约3000条，按月分片可确保零截断。
    """
    keywords = keywords or DEFAULT_KEYWORDS
    sess = session or create_cninfo_session()
    seen: Dict[str, Dict] = {}

    chunks = _date_chunks(date_from, date_to, chunk_days=20)
    for kw in keywords:
        for c_start, c_end in chunks:
            se_date = f"{c_start}~{c_end}"
            page = 1
            page_size = 30
            while True:
                payload = {
                    "pageNum": page,
                    "pageSize": page_size,
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": "",
                    "searchkey": kw,
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": se_date,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                }
                data = None
                for attempt in range(3):
                    try:
                        resp = sess.post(CNINFO_QUERY_URL, data=payload, timeout=REQUEST_TIMEOUT)
                        resp.raise_for_status()
                        data = resp.json()
                        break
                    except Exception:
                        if attempt == 2:
                            # 尝试切换代理兜底一次
                            fallback_sess = create_cninfo_session(direct=not (not sess.trust_env))
                            try:
                                resp = fallback_sess.post(CNINFO_QUERY_URL, data=payload, timeout=REQUEST_TIMEOUT)
                                resp.raise_for_status()
                                data = resp.json()
                            except Exception:
                                data = None
                        else:
                            time.sleep(0.8 * (attempt + 1))

                if not data:
                    break

                anns = data.get("announcements") or []
                if not anns:
                    break

                for a in anns:
                    ann_id = str(a.get("announcementId") or "").strip()
                    adj = str(a.get("adjunctUrl") or "").strip()
                    sec_code = str(a.get("secCode") or "").strip()
                    sec_name = str(a.get("secName") or "").strip()
                    if not ann_id or not adj or not sec_code:
                        continue
                    # 仅保留 PDF 文件
                    if not adj.lower().endswith(".pdf"):
                        continue
                    # 仅保留沪深北 A 股（6位数字股票代码）
                    if not (len(sec_code) == 6 and sec_code.isdigit()):
                        continue
                    # 排除债券代码等非A股代码
                    if not sec_code.startswith(("00", "30", "60", "68", "43", "83", "87", "92")):
                        continue

                    if ann_id in seen:
                        continue

                    raw_title = str(a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "").strip()
                    ts = a.get("announcementTime")
                    if ts:
                        ann_date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                    else:
                        # 从路径 finalpage/2026-11-23/xxx.PDF 提取日期
                        parts = adj.split("/")
                        ann_date = parts[1] if len(parts) > 2 else c_end

                    should_fetch, ann_category, fund_hint = classify_title(raw_title)
                    pdf_url = CNINFO_STATIC_BASE + adj.lstrip("/")
                    seen[ann_id] = {
                        "announcement_id": ann_id,
                        "sec_code": sec_code,
                        "sec_name": sec_name,
                        "company_name": sec_name,
                        "market": infer_market(sec_code),
                        "title": raw_title,
                        "announce_date": ann_date,
                        "pdf_url": pdf_url,
                        "adjunct_url": adj,
                        "fund_source_hint": fund_hint,
                        "ann_category": ann_category,
                        "should_fetch": should_fetch,
                    }

                total_rec = int(data.get("totalAnnouncement") or data.get("totalRecordNum") or 0)
                if progress_cb:
                    progress_cb(f"拉取目录 [{kw}] {se_date} 第{page}页，已发现 {len(seen)} 份唯一公告")
                if not data.get("hasMore") or page * page_size >= total_rec or page >= 100:
                    break
                page += 1

    # 按公告日期倒序排列
    result = sorted(seen.values(), key=lambda x: (x["announce_date"], x["announcement_id"]), reverse=True)
    return result


def download_pdf_bytes(adjunct_url: str, session: Optional[requests.Session] = None) -> bytes:
    """
    在线流式下载 PDF 到内存 bytes（不写盘）。优先 http 提速，失败切 https 或切换代理。
    """
    adj = adjunct_url.lstrip("/")
    urls = [
        f"http://static.cninfo.com.cn/{adj}",
        f"https://static.cninfo.com.cn/{adj}",
    ]
    sess = session or create_cninfo_session()
    last_err = None
    for idx, url in enumerate(urls):
        for attempt in range(2):
            try:
                r = sess.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200 and r.content[:4] == b"%PDF":
                    return r.content
                last_err = RuntimeError(f"HTTP {r.status_code}, len={len(r.content)}")
            except Exception as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
    # 兜底切换代理再试一次
    alt_sess = create_cninfo_session(direct=not (not sess.trust_env))
    r = alt_sess.get(urls[1], timeout=REQUEST_TIMEOUT)
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        return r.content
    raise RuntimeError(f"PDF下载失败: {last_err}")
