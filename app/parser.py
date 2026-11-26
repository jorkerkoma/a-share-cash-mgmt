import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import pymupdf

from app.normalizer import (
    clean_keep_spaces,
    clean_text,
    compute_maturity_status,
    estimate_end_date,
    make_fingerprint,
    normalize_fund_source,
    normalize_institution,
    normalize_product_type,
    parse_amount_wan,
    parse_date_range,
    parse_income_wan,
    parse_single_date,
    parse_yield_rate,
)


# 表格列语义映射词典（顺序即匹配优先级）
COLUMN_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("seq", ("序号", "编号")),
    ("entrusting_party", ("委托方", "认购方", "购买方", "实施主体", "投资主体", "委托人", "购买主体", "子公司名称", "公司名称")),
    ("institution", ("受托方", "受托人", "受托机构", "签约方", "签约银行", "发行主体", "发行机构", "发行方", "发行人", "合作机构", "合作方", "理财机构", "理财银行", "金融机构", "银行名称", "存款银行", "存放银行", "开户银行", "开户行", "存款机构", "销售机构", "代销机构", "机构名称", "管理人", "交易对手")),
    ("product_name", ("产品名称", "理财产品名称", "投资产品名称", "存款产品", "产品简称", "理财品种", "现金管理产品", "名称")),
    ("product_type", ("产品类型", "收益类型", "产品品种", "投资类型", "理财类型", "风险等级", "保本类型", "产品类别", "类型")),
    ("actual_income", ("实际收益", "获得收益", "已获收益", "实现收益", "投资收益", "收益金额", "实际获得收益", "利息收入", "实际利息")),
    ("unredeemed", ("逾期未收回", "未到期金额", "尚未收回", "未收回本金", "未收回金额", "期末余额")),
    ("redeem_amount", ("赎回金额", "收回本金", "赎回本金", "到期收回金额", "已收回本金", "收回金额")),
    ("amount", ("认购金额", "购买金额", "投资金额", "委托金额", "理财金额", "产品金额", "本金", "存款金额", "现金管理金额", "交易金额", "发生额", "金额", "规模")),
    ("actual_yield", ("实际年化", "实际收益率", "实际利率")),
    ("yield_rate", ("年化收益率", "预计年收益率", "预期年化", "预计年化", "预期收益", "预计收益", "收益率", "年收益率", "年化利率", "利率", "业绩比较基准", "基准收益")),
    ("start_date", ("起息日", "起始日", "开始日", "购买日", "认购日", "成立日", "存入日", "交易日", "委托理财起始", "生效日", "计息日", "投资起始")),
    ("end_date", ("到期日", "到息日", "终止日", "结束日", "赎回日", "收回日", "到账日", "委托理财终止", "投资到期")),
    ("date_range", ("起止日", "起止时间", "投资期限", "理财期限", "产品期限", "存续期", "存款期限", "实际理财天数", "理财天数", "期限")),
    ("fund_source", ("资金来源", "资金类型", "资金性质")),
    ("is_redeemed", ("是否赎回", "赎回情况", "本金收回情况", "本金及收益收回", "是否到期", "到期状态", "进展情况", "履约情况", "当前状态", "是否收回")),
    ("relation", ("关联关系", "关联交易")),
]

# 明确非理财产品表的列特征（募投项目表、专户信息表、审议表）
REJECT_TABLE_HEADERS = (
    "项目投资总额",
    "承诺投资总额",
    "拟投入募集资金",
    "募集资金承诺投资",
    "拟使用募集资金金额",
    "项目名称",
    "银行账号",
    "专户账号",
    "募集资金专户",
    "专户余额",
    "发行名称",
    "募集资金到账时间",
    "股东名称",
    "持股比例",
    "现金管理类型",  # 上交所12个月按类型聚合表（不含受托银行/产品明细）
)

# 精准的12个月/历史滚动汇总表章节正则（排除“使用期限不超过12个月”之类的授权语句）
SUMMARY_12M_REGEXES = (
    r"(?:最近|前|过去|近)(?:十二|12)个月",
    r"(?:十二|12)个月内.*(?:使用|进行|购买|现金管理|委托理财)",
    r"截至本公告.*(?:尚未到期|未到期|已到期|累计|前十二个月|最近十二个月|委托理财情况|现金管理情况)",
    r"尚未到期.*(?:理财|现金管理|存款)",
    r"未到期.*(?:理财|现金管理)",
    r"自.*决议.*(?:已到期|尚未到期|累计)",
    r"(?:此前|前期|历史).*(?:未到期|已到期|购买.*汇总|现金管理汇总)",
)

REDEEM_SECTION_HINTS = (
    r"到期赎回",
    r"到期收回",
    r"到期兑付",
    r"到期归还",
    r"赎回情况",
    r"赎回明细",
    r"收回情况",
    r"本次赎回",
    r"前期购买.*到期",
    r"现金管理产品到期",
    r"理财产品到期",
    r"产品赎回",
)

TOTAL_ROW_KEYWORDS = ("合计", "总计", "小计", "汇总", "累计", "共计")


@dataclass
class ExtractedTable:
    page_idx: int
    y0: float
    y1: float
    rows: List[List[str]]
    section_heading: str
    context_above: str
    method: str


def _extract_company_full_name(first_pages_text: str, sec_name: str) -> str:
    """
    从 PDF 前两页抽取上市公司标准主体全称，例如：濮阳惠成电子材料股份有限公司
    """
    lines = [clean_text(line) for line in (first_pages_text or "").splitlines()[:30] if clean_text(line)]
    sec_core = re.sub(r"^(?:\*?ST|S\*ST|N|C|U|W|XD|XR|DR)+", "", clean_text(sec_name))

    candidates: List[Tuple[int, str]] = []
    # 把相邻两行也拼一下，防公司全称过长换行（如深圳高速公路集团股份有限公司）
    joined_lines = list(lines[:20]) + [lines[i] + lines[i + 1] for i in range(min(15, len(lines) - 1))]

    for idx, line in enumerate(joined_lines):
        if any(bad in line for bad in ("证券股份有限公司", "律师事务所", "会计师事务所", "核查意见")):
            continue
        for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z（）()]{4,36}(?:集团股份有限公司|股份有限公司|集团有限责任公司|有限责任公司|有限公司|集团公司))", line):
            name = m.group(1)
            name = re.sub(r"^(?:根据|关于|公司|本公司|经|由|截至|编号[:：][A-Za-z0-9\-]+)", "", name)
            if len(name) < 6 or any(bad in name for bad in ("银行", "证券", "信托", "理财", "基金", "交易所", "董事会", "监会")):
                continue
            score = 0
            overlap = sum(1 for ch in set(sec_core) if ch in name)
            score += overlap * 10
            if idx < 8:
                score += 8
            if "股份" in name:
                score += 5
            if "（以下简称" in line and name in line:
                score += 6
            candidates.append((score, name))

    if candidates:
        candidates.sort(key=lambda x: (-x[0], len(x[1])))
        best_score, best_name = candidates[0]
        if best_score >= 15:
            return best_name
    return sec_name


def _is_heading_line(line: str) -> bool:
    s = clean_text(line)
    if not s or len(s) > 72:
        return False
    if re.match(r"^(?:[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|\d+[、.．](?!\d))", s):
        return True
    if len(s) <= 48 and any(
        k in s
        for k in (
            "本次理财", "本次现金管理", "本次购买", "本次委托理财", "到期赎回", "到期收回",
            "最近十二个月", "前十二个月", "尚未到期", "已到期",
        )
    ):
        return True
    return False


def _extract_tables_pymupdf(pdf_bytes: bytes) -> Tuple[str, List[ExtractedTable]]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    full_text_parts: List[str] = []
    tables_out: List[ExtractedTable] = []
    current_heading = ""

    try:
        for p_idx, page in enumerate(doc):
            page_text = page.get_text("text") or ""
            full_text_parts.append(page_text)

            blocks = page.get_text("blocks")
            text_lines: List[Tuple[float, float, str]] = []
            for b in blocks:
                if b[6] != 0:
                    continue
                by0, by1, btxt = b[1], b[3], b[4]
                for ln in btxt.splitlines():
                    ln_s = clean_keep_spaces(ln)
                    if ln_s:
                        text_lines.append((by0, by1, ln_s))
            text_lines.sort(key=lambda x: x[0])

            try:
                finder = page.find_tables()
                found = finder.tables if finder else []
            except Exception:
                found = []

            found_sorted = sorted(found, key=lambda t: t.bbox[1])
            line_cursor = 0
            for tb in found_sorted:
                ty0, ty1 = tb.bbox[1], tb.bbox[3]
                above_lines: List[str] = []
                while line_cursor < len(text_lines) and text_lines[line_cursor][0] < ty0 + 2:
                    ln_txt = text_lines[line_cursor][2]
                    if _is_heading_line(ln_txt):
                        current_heading = ln_txt
                    above_lines.append(ln_txt)
                    line_cursor += 1

                raw_rows = tb.extract() or []
                norm_rows = [
                    [clean_keep_spaces(c) if c is not None else "" for c in row]
                    for row in raw_rows
                ]
                norm_rows = [r for r in norm_rows if any(cell != "" for cell in r)]
                if not norm_rows:
                    continue

                context_above = " | ".join(above_lines[-4:])
                tables_out.append(
                    ExtractedTable(
                        page_idx=p_idx,
                        y0=ty0,
                        y1=ty1,
                        rows=norm_rows,
                        section_heading=current_heading,
                        context_above=context_above,
                        method="pymupdf_table",
                    )
                )

            while line_cursor < len(text_lines):
                ln_txt = text_lines[line_cursor][2]
                if _is_heading_line(ln_txt):
                    current_heading = ln_txt
                line_cursor += 1
    finally:
        doc.close()

    return ("\n".join(full_text_parts), tables_out)


def _extract_tables_pdfplumber(pdf_bytes: bytes) -> List[ExtractedTable]:
    tables_out: List[ExtractedTable] = []
    current_heading = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p_idx, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            for ln in txt.splitlines():
                if _is_heading_line(ln):
                    current_heading = clean_keep_spaces(ln)
            tbs = page.find_tables()
            for tb in sorted(tbs, key=lambda t: t.bbox[1]):
                raw_rows = tb.extract() or []
                norm_rows = [
                    [clean_keep_spaces(c) if c is not None else "" for c in row]
                    for row in raw_rows
                ]
                norm_rows = [r for r in norm_rows if any(c != "" for c in r)]
                if not norm_rows:
                    continue
                above_crop = page.crop((0, max(0, tb.bbox[1] - 130), page.width, tb.bbox[1]))
                above_raw = above_crop.extract_text() or ""
                for ln in above_raw.splitlines():
                    if _is_heading_line(ln):
                        current_heading = clean_keep_spaces(ln)
                tables_out.append(
                    ExtractedTable(
                        page_idx=p_idx,
                        y0=tb.bbox[1],
                        y1=tb.bbox[3],
                        rows=norm_rows,
                        section_heading=current_heading,
                        context_above=clean_keep_spaces(above_raw)[-200:],
                        method="pdfplumber_table",
                    )
                )
    return tables_out


def _map_header_columns(header_cells: List[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    cleaned = [clean_text(h) for h in header_cells]
    for col_idx, h in enumerate(cleaned):
        if not h:
            continue
        for field_name, keywords in COLUMN_PATTERNS:
            if field_name in mapping:
                continue
            if field_name == "actual_income" and ("预计" in h or "预期" in h):
                continue
            if field_name == "amount" and any(k in h for k in ("收益", "赎回", "收回", "未到期", "余额", "逾期")):
                continue
            if field_name == "yield_rate" and "实际" in h and "预期" not in h and "预计" not in h:
                continue
            if field_name == "product_name" and any(k in h for k in ("受托", "委托", "公司", "机构", "银行", "项目")):
                continue
            if field_name == "product_type" and any(k in h for k in ("资金", "关联")):
                continue
            if field_name == "date_range" and any(k in h for k in ("起息", "到期", "到息", "开始", "结束", "起始", "终止")):
                continue
            if any(k in h for k in keywords):
                mapping[field_name] = col_idx
                break
    return mapping


def _is_header_like_row(row: List[str]) -> bool:
    joined = "".join(clean_text(c) for c in row)
    if not joined:
        return False
    # 数据行特征：含日期、金额数字、收益率数字、期限数字、或典型数据值（否/是/自有资金/募集资金/保本）
    if re.search(r"20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}", joined):
        return False
    # 若任意单元格是纯数值/带千分位金额/百分比/期限（排除“12个月”表头描述），必为数据行而非表头
    for cell in row:
        c = clean_text(cell)
        if not c or "12个月" in c:
            continue
        if re.search(r"(?:^|\D)\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?:^|\D)\d+\.\d+%?|^\d+(?:天|个月|年|万元|亿元|元)$", c):
            return False
    # 只匹配真正的列标题关键词，避免“中国银行/保本浮动收益/自有资金/理财产品”等数据值误触发
    header_tokens = (
        "序号", "受托方", "受托人", "受托机构", "委托方", "签约方", "产品名称", "产品类型",
        "收益类型", "产品金额", "投资金额", "认购金额", "购买金额", "委托金额", "理财金额",
        "赎回金额", "金额（", "金额(", "预计年化", "预期年化", "年化收益率", "预计年收益率",
        "实际收益", "起息日", "到期日", "到息日", "起始日", "终止日", "产品期限", "资金来源",
        "关联交易", "关联关系", "是否赎回", "本金收回情况", "投资方向",
    )
    hits = 0
    for cell in row:
        c = clean_text(cell)
        if not c:
            continue
        if any(k in c for k in header_tokens):
            hits += 1
    return hits >= 3


def _looks_like_split_header_tail(row: List[str]) -> bool:
    """
    判断跨页首行是否是被分页腰斩的表头下半截，例如 ['称', '', '', '（万元）', '率（%）', '', '', '', '', '为关联交易']
    """
    non_empty = [clean_text(c) for c in row if clean_text(c)]
    if not non_empty:
        return False
    if any(re.search(r"20\d{2}|\d{3,}", c) for c in non_empty):
        return False
    tail_tokens = ("称", "名称", "元）", "万元）", "（万元）", "（元）", "（%）", "率（%）", "收益率", "方向", "联交易", "为关联交易", "来源", "类型", "日期", "期限", "情况", "金额")
    hits = sum(1 for c in non_empty if len(c) <= 10 and (any(t in c for t in tail_tokens) or len(c) <= 2))
    return hits >= max(1, len(non_empty) * 0.7)


def _looks_like_split_data_tail(prev_row: List[str], next_row: List[str], col_map: Dict[str, int]) -> bool:
    """
    判断跨页首行是否是上一页最后一行被分页劈开的下半截！
    例如：
      prev: ['七天通知存款', '中国建设银行股份有限公司浙江长三角一体化', ..., '4,100', ...]
      next: ['', '示范区支行', '', '', '', '', '', '', '', '']
    或者：
      prev: ['中国工商银行股份', '结构性存款', '中国工商银行区间累计型法', '1,000.00', '1.89%', '2026年5月14', '2026年11月', '全部收回', '募集资金', '否']
      next: ['有限公司江阴支行', '', '人人民币结构性存款产品-专户型2026年第294', '', '', '日', '16日', '', '', '']
    """
    # 若新行自己带有明确的金额或序号，说明是新的一行，不是续行
    amt_idx = col_map.get("amount")
    red_idx = col_map.get("redeem_amount")
    seq_idx = col_map.get("seq")
    if seq_idx is not None and seq_idx < len(next_row) and re.fullmatch(r"\d+", clean_text(next_row[seq_idx])):
        return False
    for idx in (amt_idx, red_idx):
        if idx is not None and idx < len(next_row):
            c = clean_text(next_row[idx])
            if c and re.search(r"\d", c):
                return False

    # 如果金额列在上一行有数值而在本行是空的，高度疑似被劈开的跨页行！
    prev_has_amt = False
    for idx in (amt_idx, red_idx):
        if idx is not None and idx < len(prev_row) and re.search(r"\d", clean_text(prev_row[idx])):
            prev_has_amt = True

    if prev_has_amt:
        # 只要本行没有金额，且非空列占比少于总列数，或者包含残缺日期/机构尾缀/产品尾缀，就拼接到上一行！
        non_empty_cnt = sum(1 for c in next_row if clean_text(c))
        if non_empty_cnt == 0:
            return False
        # 排除“合计”行
        first_ne = next((clean_text(c) for c in next_row if clean_text(c)), "")
        if any(first_ne.startswith(k) for k in TOTAL_ROW_KEYWORDS):
            return False
        return True

    # 兜底：检查是否存在残缺日期片段（如 '日', '16日', '11月20日' 缺年份）或尾缀词
    for c in next_row:
        ct = clean_text(c)
        if re.fullmatch(r"(?:\d{1,2}月)?\d{1,2}日|日|月", ct):
            return True
    return False


def _concat_rows(row_a: List[str], row_b: List[str]) -> List[str]:
    out = []
    n = max(len(row_a), len(row_b))
    for i in range(n):
        a = row_a[i] if i < len(row_a) else ""
        b = row_b[i] if i < len(row_b) else ""
        if a and b:
            out.append(clean_keep_spaces(f"{a}{b}"))
        else:
            out.append(a or b)
    return out


def _align_rows_to_prev_header(prev_header: List[str], tb_rows: List[List[str]]) -> Optional[List[List[str]]]:
    """
    若跨页续表因 PDF 空列导致列数略有差异（如 9 列 vs 11 列含 2 个空白列），
    但表头非空列与上一页表头完全一致，则按非空列索引映射对齐为上一页同等列数。
    """
    if not prev_header or not tb_rows:
        return None
    prev_ne = [(i, clean_text(c)) for i, c in enumerate(prev_header) if clean_text(c)]
    cur_ne = [(i, clean_text(c)) for i, c in enumerate(tb_rows[0]) if clean_text(c)]
    if not prev_ne or [x[1] for x in prev_ne] != [x[1] for x in cur_ne]:
        return None
    col_map_idx = [cur_idx for cur_idx, _ in cur_ne]
    prev_positions = [prev_idx for prev_idx, _ in prev_ne]
    target_len = len(prev_header)
    aligned: List[List[str]] = []
    for row in tb_rows:
        new_r = [""] * target_len
        for src_i, dst_i in zip(col_map_idx, prev_positions):
            if src_i < len(row):
                new_r[dst_i] = row[src_i]
        aligned.append(new_r)
    return aligned


def _merge_cross_page_tables(tables: List[ExtractedTable]) -> List[ExtractedTable]:
    """
    合并跨页拆分的同一张表格（支持跨 2 页、3 页及更多连续页面），
    同时自动缝合被分页腰斩的表头行和数据行（含下一页重复表头后紧跟残缺续行的情形）！
    """
    if not tables:
        return []
    merged: List[ExtractedTable] = [tables[0]]
    for tb in tables[1:]:
        prev = merged[-1]
        tb_rows = tb.rows
        same_cols = len(tb_rows[0]) == len(prev.rows[0])
        adjacent_page = tb.page_idx == prev.page_idx + 1
        near_top = tb.y0 < 200
        no_new_section = (not tb.section_heading) or (tb.section_heading == prev.section_heading)

        # 若因幽灵空白列导致列数不同但跨页表头一致，自动对齐列索引
        if not same_cols and adjacent_page and near_top and no_new_section:
            aligned = _align_rows_to_prev_header(prev.rows[0], tb_rows)
            if aligned:
                tb_rows = aligned
                same_cols = True

        # 特殊：2列 KV 竖表跨页续表（无新“事项X”标题）
        if len(tb_rows[0]) == 2 and same_cols and adjacent_page and near_top and no_new_section:
            prev.rows.extend(tb_rows)
            prev.page_idx = tb.page_idx
            prev.y1 = tb.y1
            continue

        if same_cols and adjacent_page and near_top and no_new_section:
            first_row = tb_rows[0]
            col_map = _map_header_columns(prev.rows[0])

            # 情况1：上一页只来得及放了1行表头，下一页第0行是表头下半截
            if len(prev.rows) == 1 and _looks_like_split_header_tail(first_row):
                prev.rows[0] = _concat_rows(prev.rows[0], first_row)
                prev.rows.extend(tb_rows[1:])
                prev.page_idx = tb.page_idx
                prev.y1 = tb.y1
                continue

            # 情况2：下一页重复了完整表头（注意：表头后的第一行 data_rows[0] 仍可能是上一页末行被腰斩的下半截！）
            if [clean_text(c) for c in first_row] == [clean_text(c) for c in prev.rows[0]] or _is_header_like_row(first_row):
                data_rows = tb_rows[1:]
                if data_rows and len(prev.rows) >= 2 and _looks_like_split_data_tail(prev.rows[-1], data_rows[0], col_map):
                    prev.rows[-1] = _concat_rows(prev.rows[-1], data_rows[0])
                    prev.rows.extend(data_rows[1:])
                else:
                    prev.rows.extend(data_rows)
                prev.page_idx = tb.page_idx
                prev.y1 = tb.y1
                continue

            # 情况3：下一页第0行是上一页最后一行数据被分页劈开的下半截
            if len(prev.rows) >= 2 and _looks_like_split_data_tail(prev.rows[-1], first_row, col_map):
                prev.rows[-1] = _concat_rows(prev.rows[-1], first_row)
                prev.rows.extend(tb_rows[1:])
                prev.page_idx = tb.page_idx
                prev.y1 = tb.y1
                continue

            prev.rows.extend(tb_rows)
            prev.page_idx = tb.page_idx
            prev.y1 = tb.y1
            continue

        merged.append(tb)
    return merged


def _prepare_table_header_and_body(rows: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
    if not rows:
        return ([], [])
    header = list(rows[0])
    start_idx = 1

    if len(rows) > 1 and not _is_header_like_row(header) and _is_header_like_row(rows[1]):
        header = list(rows[1])
        start_idx = 2

    if len(rows) > start_idx and _looks_like_split_header_tail(rows[start_idx]):
        header = _concat_rows(header, rows[start_idx])
        start_idx += 1

    return (header, rows[start_idx:])


def _is_summary_12m_context(section_heading: str, context_above: str) -> bool:
    for text in (clean_text(section_heading), clean_text(context_above)):
        if not text:
            continue
        # 剔除授权期限短语的干扰，例如“使用期限不超过12个月”“有效期12个月”
        sanitized = re.sub(r"(?:使用期限|投资期限|有效期限|有效期|授权期限|决议通过之日起|审议通过之日起)[^。；，]{0,18}(?:十二|12)个月", "", text)
        sanitized = re.sub(r"不超过(?:十二|12)个月", "", sanitized)
        for pat in SUMMARY_12M_REGEXES:
            if re.search(pat, sanitized):
                return True
    return False


def _classify_table(
    header: List[str],
    col_map: Dict[str, int],
    section_heading: str,
    context_above: str,
    ann_category: str,
) -> str:
    hdr_joined = clean_text("".join(header))
    if any(bad in hdr_joined for bad in REJECT_TABLE_HEADERS):
        if "受托" not in hdr_joined and "理财产品" not in hdr_joined and "结构性存款" not in hdr_joined:
            return "ignore"

    has_entity = ("institution" in col_map) or ("product_name" in col_map)
    has_measure = any(
        k in col_map for k in ("amount", "redeem_amount", "yield_rate", "start_date", "end_date", "date_range", "actual_income")
    )
    if not (has_entity and has_measure):
        return "ignore"
    if ("institution" not in col_map) and ("amount" not in col_map) and ("redeem_amount" not in col_map):
        return "ignore"

    # 1. 强信号判断 12个月/历史滚动汇总表
    if _is_summary_12m_context(section_heading, context_above):
        return "summary_12m"
    # 凡是含“是否赎回 / 到期状态 / 本金收回情况”或“未到期金额+赎回金额”的，均为滚动汇总表
    if "is_redeemed" in col_map:
        return "summary_12m"
    if "unredeemed" in col_map and ("redeem_amount" in col_map or "actual_income" in col_map):
        return "summary_12m"

    # 2. 判断是否为本次到期赎回表
    above_clean = clean_text(context_above)
    sec_clean = clean_text(section_heading)
    buy_override_near = any(k in above_clean[-45:] for k in ("本次购买", "本次使用", "继续进行", "新购", "继续购买", "本次认购"))
    if any(re.search(h, above_clean) for h in REDEEM_SECTION_HINTS) and not buy_override_near:
        return "redeem"
    if "redeem_amount" in col_map or "actual_income" in col_map or "actual_yield" in col_map:
        return "redeem"
    if any(re.search(h, sec_clean) for h in REDEEM_SECTION_HINTS) and not buy_override_near:
        return "redeem"
    if ann_category == "到期赎回" and not any(k in f"{sec_clean}{above_clean}" for k in ("继续购买", "本次购买", "新购", "继续进行")):
        return "redeem"

    return "buy"


def _detect_unit_hint(context_above: str) -> str:
    m = re.search(r"单位[：:]\s*([人民币美元港元港币万亿元]+)", clean_text(context_above))
    return m.group(1) if m else ""


def _try_parse_kv_table(
    ann: Dict[str, Any],
    company_name: str,
    full_text: str,
    table: ExtractedTable,
) -> List[Dict[str, Any]]:
    """
    解析 2 列或 4 列的竖排键值对明细表（如森霸传感：签约方名称 | 招商证券股份有限公司 ...）
    """
    if not table.rows:
        return []
    ncols = len(table.rows[0])
    if ncols not in (2, 4):
        return []

    kv: Dict[str, str] = {}
    for row in table.rows:
        pairs = [(row[0], row[1])] if ncols == 2 else [(row[0], row[1]), (row[2], row[3])]
        for k_raw, v_raw in pairs:
            k = clean_text(k_raw)
            v = clean_keep_spaces(v_raw)
            if not k or not v:
                continue
            kv[k] = v

    if len(kv) < 4:
        return []

    # 排除募投/IPO概况表
    if any(bad in "".join(kv.keys()) for bad in ("募集资金总额", "募集资金净额", "发行名称", "保荐机构")):
        return []

    def find_val(keywords: Tuple[str, ...]) -> Tuple[str, str]:
        for k, v in kv.items():
            if any(w in k for w in keywords):
                return (k, v)
        return ("", "")

    _, inst_raw = find_val(("受托方", "受托人", "签约方", "发行机构", "银行", "合作机构", "管理人"))
    _, prod_name = find_val(("产品名称", "理财产品名称", "投资产品"))
    _, prod_type_raw = find_val(("产品类型", "收益类型", "产品品种"))
    amt_k, amt_raw = find_val(("认购金额", "购买金额", "投资金额", "委托金额", "产品金额", "金额", "本金", "赎回金额"))
    _, curr_raw = find_val(("币种",))
    _, rate_raw = find_val(("年化收益率", "预计年收益率", "预期年化", "收益率", "年化利率"))
    _, start_raw = find_val(("起息日", "起始日", "开始日", "购买日", "认购日", "投资起始"))
    _, end_raw = find_val(("到期日", "到息日", "终止日", "赎回日", "投资到期"))
    _, term_raw = find_val(("产品期限", "投资期限", "理财期限", "理财天数", "期限"))
    _, fund_raw = find_val(("资金来源", "资金性质"))
    inc_k, inc_raw = find_val(("实际收益", "获得收益", "投资收益"))
    _, entr_raw = find_val(("委托方", "购买方", "实施主体"))

    if not inst_raw or not amt_raw:
        return []

    amount_wan, currency = parse_amount_wan(amt_raw, f"{amt_k} {curr_raw}")
    if amount_wan is None:
        return []

    inst_type, inst_main, branch_name = normalize_institution(inst_raw, full_text)
    y_disp, y_min, y_max = parse_yield_rate(rate_raw)
    s_disp, s_iso = parse_single_date(start_raw)
    e_disp, e_iso = parse_single_date(end_raw)
    if not e_iso and s_iso and term_raw:
        est = estimate_end_date(s_iso, term_raw)
        if est:
            e_iso = est
            if not e_disp:
                e_disp = f"{est}(按{term_raw}推算)"

    rec_type = "到期赎回" if (inc_raw or any(re.search(h, clean_text(table.section_heading)) for h in REDEEM_SECTION_HINTS)) else "本次新购"
    income_wan = parse_income_wan(inc_raw, inc_k, amount_wan) if inc_raw else None

    rec: Dict[str, Any] = {
        "announcement_id": ann["announcement_id"],
        "sec_code": ann["sec_code"],
        "sec_name": ann["sec_name"],
        "company_name": company_name,
        "market": ann["market"],
        "announce_date": ann["announce_date"],
        "entrusting_party": entr_raw or company_name,
        "record_type": rec_type,
        "maturity_status": compute_maturity_status(rec_type, e_disp, e_iso, term_raw),
        "institution_raw": inst_raw,
        "institution_type": inst_type,
        "institution_main": inst_main or inst_raw,
        "branch_name": branch_name,
        "product_name": prod_name or prod_type_raw,
        "product_type": normalize_product_type(prod_name, prod_type_raw),
        "product_type_raw": prod_type_raw,
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
        "fund_source": normalize_fund_source(fund_raw, ann.get("fund_source_hint", "")),
        "section_title": table.section_heading[:80] or "竖排明细表",
        "pdf_url": ann["pdf_url"],
        "ann_title": ann["title"],
        "parse_method": f"{table.method}_kv",
    }
    rec["_dup_seq"] = 0
    rec["fingerprint"] = make_fingerprint(rec)
    rec.pop("_dup_seq", None)
    return [rec]


def _rows_to_records(
    ann: Dict[str, Any],
    company_name: str,
    full_text: str,
    table: ExtractedTable,
    header: List[str],
    body_rows: List[List[str]],
    col_map: Dict[str, int],
    record_type: str,
    only_recent_window: bool = False,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    unit_hint = _detect_unit_hint(table.context_above)

    last_entrusting = ""
    last_institution = ""
    last_fund_source = ""
    dup_counter: Dict[str, int] = {}

    def get_cell(row: List[str], field: str) -> str:
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return ""
        return clean_keep_spaces(row[idx])

    def get_hdr(field: str) -> str:
        idx = col_map.get(field)
        if idx is None or idx >= len(header):
            return ""
        return clean_keep_spaces(header[idx])

    for row in body_rows:
        row_joined = clean_text("".join(row))
        if not row_joined:
            continue
        first_non_empty = next((clean_text(c) for c in row if clean_text(c)), "")
        if any(first_non_empty.startswith(k) or k in first_non_empty for k in TOTAL_ROW_KEYWORDS):
            continue
        if first_non_empty.startswith(("注：", "注:", "说明：", "备注：")):
            continue
        if _is_header_like_row(row):
            # 若表内中途出现与原表头不同的第二段横排子表头（上交所常见的“12列拆上下两半6列”堆叠表，如沪光股份），立即终止防止列错位！
            orig_hdr_clean = [clean_text(h) for h in header]
            cur_row_clean = [clean_text(c) for c in row]
            if cur_row_clean != orig_hdr_clean and any(
                k in "".join(cur_row_clean) for k in ("逾期未收回", "是否构成关联交易", "预计收益金额", "资金来源", "实际收益")
            ) and not any(k in "".join(orig_hdr_clean) for k in ("逾期未收回", "是否构成关联交易")):
                break
            continue
        if _looks_like_split_header_tail(row):
            continue

        entrusting = get_cell(row, "entrusting_party")
        inst_raw = get_cell(row, "institution")
        prod_name = get_cell(row, "product_name")
        prod_type_raw = get_cell(row, "product_type")
        amt_raw = get_cell(row, "amount") or get_cell(row, "redeem_amount")
        if record_type == "到期赎回" and get_cell(row, "redeem_amount"):
            amt_raw = get_cell(row, "redeem_amount") or amt_raw
        amt_hdr = get_hdr("amount") if get_cell(row, "amount") else get_hdr("redeem_amount")
        income_raw = get_cell(row, "actual_income")
        income_hdr = get_hdr("actual_income")
        yield_raw = get_cell(row, "yield_rate")
        actual_yield_raw = get_cell(row, "actual_yield")
        start_raw = get_cell(row, "start_date")
        end_raw = get_cell(row, "end_date")
        range_raw = get_cell(row, "date_range")
        fund_raw = get_cell(row, "fund_source")
        redeemed_flag = get_cell(row, "is_redeemed")

        if not (prod_name or amt_raw or inst_raw):
            continue

        if not entrusting and last_entrusting and (prod_name or amt_raw):
            entrusting = last_entrusting
        elif entrusting:
            last_entrusting = entrusting

        if inst_raw in ("行", "支行", "分行", "有限公司", "股份有限公司"):
            inst_raw = ""
        if not inst_raw and last_institution and (prod_name or amt_raw):
            inst_raw = last_institution
        elif inst_raw:
            last_institution = inst_raw

        if not fund_raw and last_fund_source and (prod_name or amt_raw):
            fund_raw = last_fund_source
        elif fund_raw:
            last_fund_source = fund_raw

        amount_wan, currency = parse_amount_wan(amt_raw, amt_hdr, unit_hint)
        # 严控碎片行与0元错位行（正常单笔购买/赎回金额必须大于 0）
        if amount_wan is None or amount_wan <= 0:
            continue

        if not prod_name and prod_type_raw:
            prod_name = prod_type_raw

        start_disp, start_iso = parse_single_date(start_raw)
        end_disp, end_iso = parse_single_date(end_raw)
        term_raw = ""
        if range_raw:
            r_s, r_e, r_e_iso, r_term = parse_date_range(range_raw)
            if not start_disp and r_s:
                start_disp = r_s
                start_iso = r_s
            if not end_disp and r_e:
                end_disp = r_e
                end_iso = r_e_iso
            if r_term:
                term_raw = r_term
                # 若表头写了“产品期限（天）”而单元格是纯数字“14”，补上“天”
                if re.fullmatch(r"\d+", term_raw) and "天" in get_hdr("date_range"):
                    term_raw = f"{term_raw}天"

        if not end_iso and term_raw:
            base_dt = start_iso or ann.get("announce_date", "")
            est_iso = estimate_end_date(base_dt, term_raw)
            if est_iso:
                end_iso = est_iso
                if not end_disp:
                    tag = f"按{term_raw}推算" if start_iso else f"约{est_iso}({term_raw})"
                    end_disp = f"{est_iso}({tag})" if start_iso else tag

        if only_recent_window:
            ann_dt = datetime.strptime(ann["announce_date"], "%Y-%m-%d").date()
            in_window = False
            for d_str in (start_iso, end_iso):
                if d_str:
                    try:
                        d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
                        if -35 <= (d_obj - ann_dt).days <= 7:
                            in_window = True
                    except ValueError:
                        pass
            if not in_window:
                continue
            if any(k in redeemed_flag for k in ("是", "已到期", "已赎回", "全部收回", "收回", "到期")) or income_raw:
                row_rec_type = "到期赎回"
            else:
                row_rec_type = "本次新购"
        else:
            row_rec_type = record_type

        y_disp, y_min, y_max = parse_yield_rate(yield_raw)
        _, ay_min, ay_max = parse_yield_rate(actual_yield_raw)
        actual_yield_pct = ay_max if ay_max is not None else ay_min
        if not y_disp and actual_yield_raw:
            y_disp, y_min, y_max = parse_yield_rate(actual_yield_raw)

        actual_income_wan = parse_income_wan(income_raw, income_hdr, amount_wan, unit_hint) if income_raw else None

        inst_type, inst_main, branch_name = normalize_institution(inst_raw, full_text)
        # 仅当受托方列缺失且产品名中明确含真实银行/券商/理财子名时才从产品名兜底，绝不把产品后缀当作分支行
        if not inst_main and prod_name:
            cand_type, cand_main, cand_branch = normalize_institution(prod_name, full_text)
            if cand_type != "其他" and cand_main:
                if cand_branch and not re.search(r"(?:分行营业部|支行营业部|分行|支行|营业部|营业室|分理处)$", cand_branch):
                    cand_branch = "未披露"
                inst_type, inst_main, branch_name = cand_type, cand_main, cand_branch
            else:
                inst_type, inst_main, branch_name = "其他", "未披露", "未披露"

        prod_type_std = normalize_product_type(prod_name, prod_type_raw)
        maturity_status = compute_maturity_status(row_rec_type, end_disp, end_iso, term_raw)
        fund_source = normalize_fund_source(fund_raw, ann.get("fund_source_hint", ""))

        rec: Dict[str, Any] = {
            "announcement_id": ann["announcement_id"],
            "sec_code": ann["sec_code"],
            "sec_name": ann["sec_name"],
            "company_name": company_name,
            "market": ann["market"],
            "announce_date": ann["announce_date"],
            "entrusting_party": entrusting or company_name,
            "record_type": row_rec_type,
            "maturity_status": maturity_status,
            "institution_raw": inst_raw,
            "institution_type": inst_type,
            "institution_main": inst_main or inst_raw,
            "branch_name": branch_name,
            "product_name": prod_name,
            "product_type": prod_type_std,
            "product_type_raw": prod_type_raw,
            "amount_wan": amount_wan,
            "amount_raw": amt_raw,
            "currency": currency,
            "yield_rate_raw": y_disp,
            "yield_min": y_min,
            "yield_max": y_max,
            "actual_income_wan": actual_income_wan,
            "actual_yield_pct": actual_yield_pct,
            "start_date": start_disp,
            "end_date": end_disp or term_raw,
            "end_date_iso": end_iso,
            "term_raw": term_raw,
            "fund_source": fund_source,
            "section_title": table.section_heading[:80],
            "pdf_url": ann["pdf_url"],
            "ann_title": ann["title"],
            "parse_method": table.method,
        }

        base_k = f"{rec['record_type']}|{rec['institution_main']}|{rec['product_name']}|{rec['amount_wan']}|{rec['start_date']}|{rec['end_date']}"
        dup_counter[base_k] = dup_counter.get(base_k, 0) + 1
        rec["_dup_seq"] = dup_counter[base_k] - 1
        rec["fingerprint"] = make_fingerprint(rec)
        rec.pop("_dup_seq", None)

        records.append(rec)

    return records


def _extract_from_prose(ann: Dict[str, Any], company_name: str, full_text: str) -> List[Dict[str, Any]]:
    """
    兜底解析：针对没有画表格、仅用文字段落披露单笔购买/赎回的公告（如 600713 南京医药 等）
    """
    clean_body = re.sub(r"\s+", "", full_text)
    # 保留换行边界的文本，防止正文键值对“（二）签约银行：... \n（三）产品类型：...”跨行粘连
    line_body = "\n".join(re.sub(r"\s+", "", ln) for ln in (full_text or "").splitlines() if ln.strip())
    records: List[Dict[str, Any]] = []

    # 1. 键值对正文（含“1、产品名称：...”）
    kv_inst = re.search(r"(?:受托方|受托人|签约方名称|发行机构|签约银行|开户银行|合作银行)[：:]([^\n。；;，,]{2,40})", line_body)
    kv_prod = re.search(r"(?:产品名称|理财产品名称|投资产品)[：:]([^\n。；;，,]{2,50})", line_body)
    kv_amt = re.search(r"(?:认购金额|购买金额|投资金额|委托金额|产品金额|本金|赎回金额)[：:](?:人民币)?([\d,.]+\s*[万亿]?元?)", line_body)
    kv_rate = re.search(r"(?:预期年化收益率|预计年化收益率|预计年收益率|年化收益率|年收益率|预期收益率)[：:]([^\n。；;，,]{1,35})", line_body)
    kv_start = re.search(r"(?:起息日|起始日|认购日|购买日|成立日|投资起始日)[：:](20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?)", line_body)
    kv_end = re.search(r"(?:到期日|终止日|赎回日|投资到期日)[：:](20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?|随时[^\n。；;，,]{1,10}|无固定期限)", line_body)
    kv_term = re.search(r"(?:产品期限|存款期限|投资期限)[：:]([^\n。；;，,]{1,20})", line_body)
    kv_income = re.search(r"(?:实际收益|获得收益|投资收益|理财收益|利息收入)[：:](?:人民币)?([\d,.]+\s*[万亿]?元?)", line_body)

    if kv_inst and kv_amt:
        inst_raw = re.split(r"[（(][一二三四五六七八九十\d]+[）)]", kv_inst.group(1))[0].strip()
        prod_name = re.split(r"[（(][一二三四五六七八九十\d]+[）)]", kv_prod.group(1))[0].strip() if kv_prod else ""
        amt_raw = kv_amt.group(1)
        amount_wan, currency = parse_amount_wan(amt_raw)
        if amount_wan is not None:
            inst_type, inst_main, branch_name = normalize_institution(inst_raw, full_text)
            y_disp, y_min, y_max = parse_yield_rate(kv_rate.group(1) if kv_rate else "")
            s_disp, s_iso = parse_single_date(kv_start.group(1) if kv_start else "")
            e_disp, e_iso = parse_single_date(kv_end.group(1) if kv_end else "")
            term_raw = kv_term.group(1) if kv_term else ""
            if not e_iso and s_iso and term_raw:
                e_iso = estimate_end_date(s_iso, term_raw)
            rec_type = "到期赎回" if (ann.get("ann_category") == "到期赎回" or kv_income) else "本次新购"
            income_wan = parse_income_wan(kv_income.group(1), "", amount_wan) if kv_income else None
            rec = {
                "announcement_id": ann["announcement_id"],
                "sec_code": ann["sec_code"],
                "sec_name": ann["sec_name"],
                "company_name": company_name,
                "market": ann["market"],
                "announce_date": ann["announce_date"],
                "entrusting_party": company_name,
                "record_type": rec_type,
                "maturity_status": compute_maturity_status(rec_type, e_disp, e_iso, term_raw),
                "institution_raw": inst_raw,
                "institution_type": inst_type,
                "institution_main": inst_main or inst_raw,
                "branch_name": branch_name,
                "product_name": prod_name,
                "product_type": normalize_product_type(prod_name, ""),
                "product_type_raw": "",
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
                "fund_source": normalize_fund_source("", ann.get("fund_source_hint", "")),
                "section_title": "正文键值对提取",
                "pdf_url": ann["pdf_url"],
                "ann_title": ann["title"],
                "parse_method": "prose_regex",
            }
            rec["_dup_seq"] = 0
            rec["fingerprint"] = make_fingerprint(rec)
            rec.pop("_dup_seq", None)
            records.append(rec)
            return records

    # 2. 句式A：“向/在/与XX银行(XX支行)购买/认购/赎回...金额XX万元”
    # 3. 句式B：“使用...资金XX万元购买/认购/存入XX银行(XX支行)单位大额存单...” (如 600713 南京医药)
    pat_a = re.compile(
        r"(?:向|在|与|通过)([\u4e00-\u9fa5A-Za-z]{2,26}(?:银行|证券|理财|信托)(?:股份有限公司|有限责任公司|有限公司)?(?:[\u4e00-\u9fa5]{2,14}(?:分行|支行|营业部))?)"
        r"[^。；]{0,35}?(?:购买|认购|办理|存入|赎回|收回)[^。；]{0,40}?"
        r"(?:金额|本金|人民币)?(?:为)?([\d,.]+)\s*(万元|亿元|元)"
    )
    pat_b = re.compile(
        r"(?:使用|以)[^。；]{0,28}?(?:人民币)?([\d,.]+)\s*(万元|亿元|元)[^。；]{0,12}?(?:购买|认购|办理|存入)了?"
        r"([\u4e00-\u9fa5A-Za-z]{2,24}(?:银行|证券|理财|信托)(?:股份有限公司|有限责任公司|有限公司)?(?:[\u4e00-\u9fa5]{2,14}(?:分行|支行|营业部))?)"
        r"([^。；，,]{0,40})"
    )

    matches: List[Tuple[int, int, str, str, str]] = []
    for m in pat_a.finditer(clean_body):
        matches.append((m.start(), m.end(), m.group(1), f"{m.group(2)}{m.group(3)}", ""))
    if not matches:
        for m in pat_b.finditer(clean_body):
            matches.append((m.start(), m.end(), m.group(3), f"{m.group(1)}{m.group(2)}", m.group(4)))

    for idx, (ms, me, inst_raw, amt_raw, tail_prod) in enumerate(matches):
        span_start = max(0, ms - 40)
        span_end = min(len(clean_body), me + 260)
        snippet = clean_body[span_start:span_end]
        if any(q in snippet[:120] for q in ("不超过", "额度", "拟使用", "授权", "审议通过", "最高额")):
            continue
        amount_wan, currency = parse_amount_wan(amt_raw)
        if amount_wan is None or amount_wan <= 0:
            continue

        # 优先使用紧跟受托方之后的产品描述（避免误抓“以下简称‘XX子公司’”的引号）
        prod_name = ""
        tail_clean = tail_prod.strip("的 ，,。")
        if tail_clean and any(kw in tail_clean for kw in ("存单", "存款", "理财", "凭证", "计划", "基金", "回购", "信托", "期")):
            prod_name = tail_clean
        if not prod_name:
            for qm in re.finditer(r"(?<!简称)[“《\"]([^”》\"]{2,40})[”》\"]", snippet):
                cand = qm.group(1)
                if cand != "公司" and not cand.endswith(("公司", "有限")):
                    prod_name = cand
                    break
        if not prod_name or len(prod_name) < 2:
            for kw in ("结构性存款", "大额存单", "通知存款", "定期存款", "协定存款", "收益凭证", "国债逆回购", "理财产品"):
                if kw in snippet:
                    prod_name = kw
                    break

        rate_m = re.search(r"(?:收益率|年化|利率)(?:（年化）|\(年化\))?[^\d]{0,8}([\d.]+%?(?:\s*[-~至—/或]\s*[\d.]+%?)?)", snippet)
        y_disp, y_min, y_max = parse_yield_rate(rate_m.group(1) if rate_m else "")

        s_m = re.search(r"(?:起息日|起始日|购买日|认购日)(?:为)?(20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?)", snippet)
        e_m = re.search(r"(?:到期日|终止日|赎回日)(?:为)?(20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?)", snippet)
        if s_m or e_m:
            s_disp, s_iso = parse_single_date(s_m.group(1)) if s_m else ("", "")
            e_disp, e_iso = parse_single_date(e_m.group(1)) if e_m else ("", "")
        else:
            dates = re.findall(r"20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?", snippet)
            s_disp, s_iso = parse_single_date(dates[0]) if len(dates) >= 1 else ("", "")
            e_disp, e_iso = parse_single_date(dates[1]) if len(dates) >= 2 else ("", "")

        term_m = re.search(r"(?:期限|共)[^\d]{0,4}(\d+[天日个年]+月?)", snippet)
        term_raw = term_m.group(1) if term_m else ""
        if not e_iso and s_iso and term_raw:
            e_iso = estimate_end_date(s_iso, term_raw)

        inc_m = re.search(r"(?:实际收益|获得收益|获得理财收益|理财收益|投资收益|利息)[^\d]{0,8}([\d,.]+)\s*(万元|元)", snippet)
        income_wan = parse_income_wan(f"{inc_m.group(1)}{inc_m.group(2)}", "", amount_wan) if inc_m else None

        is_redeem = any(k in snippet for k in ("已赎回", "到期赎回", "到期收回", "到期兑付", "已收回本金")) or (income_wan is not None) or (ann.get("ann_category") == "到期赎回")
        rec_type = "到期赎回" if is_redeem else "本次新购"
        inst_type, inst_main, branch_name = normalize_institution(inst_raw, full_text)

        rec = {
            "announcement_id": ann["announcement_id"],
            "sec_code": ann["sec_code"],
            "sec_name": ann["sec_name"],
            "company_name": company_name,
            "market": ann["market"],
            "announce_date": ann["announce_date"],
            "entrusting_party": company_name,
            "record_type": rec_type,
            "maturity_status": compute_maturity_status(rec_type, e_disp, e_iso, term_raw),
            "institution_raw": inst_raw,
            "institution_type": inst_type,
            "institution_main": inst_main or inst_raw,
            "branch_name": branch_name,
            "product_name": prod_name,
            "product_type": normalize_product_type(prod_name, ""),
            "product_type_raw": "",
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
            "fund_source": normalize_fund_source(snippet, ann.get("fund_source_hint", "")),
            "section_title": "正文语义提取",
            "pdf_url": ann["pdf_url"],
            "ann_title": ann["title"],
            "parse_method": "prose_regex",
        }
        rec["_dup_seq"] = idx
        rec["fingerprint"] = make_fingerprint(rec)
        rec.pop("_dup_seq", None)
        records.append(rec)

    return records


def _looks_like_quota_only(title: str, full_text: str) -> bool:
    t = clean_text(title)
    body = clean_text(full_text[:3500])
    if any(k in t for k in ("进展", "赎回", "收回", "兑付", "到期")):
        return False
    quota_signals = sum(
        1
        for k in (
            "拟使用", "拟利用", "不超过人民币", "不超过", "投资额度", "预计委托理财额度",
            "授权董事长", "授权管理层", "有效期自", "审议通过了", "投资品种",
            "额度内可循环", "循环滚动使用", "滚动使用", "委托理财目的", "增加使用", "额度内",
        )
        if k in body
    )
    # 既然表格引擎已经跑完且未提取到任何产品记录，只要文本含明显额度审议信号即归为额度授权公告
    return quota_signals >= 3


def parse_pdf_announcement(
    ann: Dict[str, Any], pdf_bytes: bytes, llm_fallback_fn=None
) -> Tuple[str, str, str, List[Dict[str, Any]]]:
    full_text, raw_tables = _extract_tables_pymupdf(pdf_bytes)
    clean_len = len(clean_text(full_text))
    if clean_len < 60:
        return (ann["sec_name"], "needs_review", "扫描件或无可用文字层", [])

    company_name = _extract_company_full_name(full_text[:2200], ann["sec_name"])
    if ann.get("fund_source_hint") in ("", "未注明"):
        ann = dict(ann)
        ann["fund_source_hint"] = normalize_fund_source(full_text[:1500])

    def run_table_pipeline(tables: List[ExtractedTable]) -> List[Dict[str, Any]]:
        merged = _merge_cross_page_tables(tables)
        extracted: List[Dict[str, Any]] = []
        summary_tables: List[Tuple[ExtractedTable, List[str], List[List[str]], Dict[str, int]]] = []

        for tb in merged:
            # 优先识别 2列 / 4列 竖排键值对明细表（如森霸传感）
            if tb.rows and len(tb.rows[0]) in (2, 4):
                kv_recs = _try_parse_kv_table(ann, company_name, full_text, tb)
                if kv_recs:
                    extracted.extend(kv_recs)
                    continue

            header, body_rows = _prepare_table_header_and_body(tb.rows)
            if not header or not body_rows:
                continue
            col_map = _map_header_columns(header)
            t_kind = _classify_table(
                header, col_map, tb.section_heading, tb.context_above, ann.get("ann_category", "")
            )
            if t_kind == "ignore":
                continue
            if t_kind == "summary_12m":
                summary_tables.append((tb, header, body_rows, col_map))
                continue
            rec_type = "到期赎回" if t_kind == "redeem" else "本次新购"
            recs = _rows_to_records(ann, company_name, full_text, tb, header, body_rows, col_map, rec_type)
            extracted.extend(recs)

        # 若整份公告没抓到常规新购/赎回表：
        # 先尝试正文语义提取（如 600713 南京医药：正文写赎回，下面附12个月按类型汇总表）
        if not extracted and summary_tables:
            prose_first = _extract_from_prose(ann, company_name, full_text)
            if prose_first:
                return prose_first
            for tb, header, body_rows, col_map in summary_tables:
                recs = _rows_to_records(
                    ann, company_name, full_text, tb, header, body_rows, col_map, "本次新购", only_recent_window=True
                )
                extracted.extend(recs)

        return extracted

    records = run_table_pipeline(raw_tables)

    if not records:
        try:
            pb_tables = _extract_tables_pdfplumber(pdf_bytes)
            if pb_tables:
                records = run_table_pipeline(pb_tables)
        except Exception:
            pass

    if not records and _looks_like_quota_only(ann["title"], full_text):
        return (company_name, "quota_only", "额度授权/审议类公告，无具体购买明细", [])

    if not records:
        records = _extract_from_prose(ann, company_name, full_text)

    if not records and llm_fallback_fn is not None:
        try:
            llm_recs = llm_fallback_fn(ann, company_name, full_text)
            if llm_recs:
                records = llm_recs
        except Exception:
            pass

    if records:
        return (company_name, "parsed", f"提取{len(records)}条记录", records)

    if ann.get("ann_category") in ("待识别", "额度授权") and any(
        k in clean_text(full_text[:2500]) for k in ("拟使用", "拟利用", "额度", "授权", "议案", "预计委托理财")
    ):
        return (company_name, "quota_only", "额度授权类公告（未含单笔产品明细）", [])

    return (company_name, "needs_review", "未识别到标准理财明细表（可能为复杂/无框排版）", [])
