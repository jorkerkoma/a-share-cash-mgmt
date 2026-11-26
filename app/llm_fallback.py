import json
from typing import Any, Dict, List

import requests

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL
from app.normalizer import (
    compute_maturity_status,
    estimate_end_date,
    make_fingerprint,
    normalize_fund_source,
    normalize_institution,
    normalize_product_type,
    parse_amount_wan,
    parse_income_wan,
    parse_single_date,
    parse_yield_rate,
)


PROMPT_TEMPLATE = """你是A股上市公司公告结构化提取助手。请从下面的现金管理/委托理财公告文本中，仅提取【本次新购买】和【本次到期赎回】的具体理财产品明细（严禁提取文末“最近十二个月累计汇总表”里的历史重复记录；如果是纯额度授权公告没有具体产品，返回空数组 []）。

严格返回 JSON 数组，每个元素包含以下字段（缺失填空字符串或 null）：
- record_type: "本次新购" 或 "到期赎回"
- entrusting_party: 委托方/子公司名称
- institution_raw: 受托方完整名称（尽量保留分行/支行/营业部名称）
- product_name: 产品名称
- product_type_raw: 原文产品类型或收益类型
- amount_raw: 购买或赎回金额（带单位，如"5000万元"）
- yield_rate_raw: 预期年化收益率或区间（如"1.05%-2.35%"）
- actual_income_raw: 实际收益（如有，带单位）
- start_date: 起息日（YYYY-MM-DD）
- end_date: 到期日（YYYY-MM-DD 或 "随时转让" 等原文）
- term_raw: 产品期限（如"90天"）
- fund_source: 资金来源（"闲置募集资金" 或 "闲置自有资金"）

公告标题：{title}
公告正文（截断前6000字）：
{body}
"""


def llm_extract_records(ann: Dict[str, Any], company_name: str, full_text: str) -> List[Dict[str, Any]]:
    if not LLM_ENABLED or not LLM_API_KEY:
        return []

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    prompt = PROMPT_TEMPLATE.format(title=ann["title"], body=full_text[:6000])
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 2000,
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    start = content.find("[")
    end = content.rfind("]")
    if start == -1 or end == -1:
        return []
    items = json.loads(content[start : end + 1])
    if not isinstance(items, list):
        return []

    records: List[Dict[str, Any]] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rec_type = "到期赎回" if "赎回" in str(it.get("record_type") or "") else "本次新购"
        inst_raw = str(it.get("institution_raw") or "").strip()
        prod_name = str(it.get("product_name") or "").strip()
        amt_raw = str(it.get("amount_raw") or "").strip()
        amount_wan, currency = parse_amount_wan(amt_raw)
        if amount_wan is None and not inst_raw:
            continue
        inst_type, inst_main, branch_name = normalize_institution(inst_raw, full_text)
        y_disp, y_min, y_max = parse_yield_rate(str(it.get("yield_rate_raw") or ""))
        s_disp, s_iso = parse_single_date(str(it.get("start_date") or ""))
        e_disp, e_iso = parse_single_date(str(it.get("end_date") or ""))
        term_raw = str(it.get("term_raw") or "").strip()
        if not e_iso and s_iso and term_raw:
            e_iso = estimate_end_date(s_iso, term_raw)
        inc_raw = str(it.get("actual_income_raw") or "").strip()
        income_wan = parse_income_wan(inc_raw, "", amount_wan) if inc_raw else None

        rec = {
            "announcement_id": ann["announcement_id"],
            "sec_code": ann["sec_code"],
            "sec_name": ann["sec_name"],
            "company_name": company_name,
            "market": ann["market"],
            "announce_date": ann["announce_date"],
            "entrusting_party": str(it.get("entrusting_party") or company_name),
            "record_type": rec_type,
            "maturity_status": compute_maturity_status(rec_type, e_disp, e_iso, term_raw),
            "institution_raw": inst_raw,
            "institution_type": inst_type,
            "institution_main": inst_main or inst_raw,
            "branch_name": branch_name,
            "product_name": prod_name,
            "product_type": normalize_product_type(prod_name, str(it.get("product_type_raw") or "")),
            "product_type_raw": str(it.get("product_type_raw") or ""),
            "amount_wan": amount_wan,
            "amount_raw": amt_raw,
            "currency": currency,
            "yield_rate_raw": y_disp,
            "yield_min": y_min,
            "yield_max": y_max,
            "actual_income_wan": income_wan,
            "actual_yield_pct": None,
            "start_date": s_disp,
            "end_date": e_disp or term_raw,
            "end_date_iso": e_iso,
            "term_raw": term_raw,
            "fund_source": normalize_fund_source(str(it.get("fund_source") or ""), ann.get("fund_source_hint", "")),
            "section_title": "AI辅助解析",
            "pdf_url": ann["pdf_url"],
            "ann_title": ann["title"],
            "parse_method": "llm",
        }
        rec["_dup_seq"] = idx
        rec["fingerprint"] = make_fingerprint(rec)
        rec.pop("_dup_seq", None)
        records.append(rec)

    return records
