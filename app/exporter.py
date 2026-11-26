import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.config import DB_PATH, OUTPUT_DIR
from app.db import get_rankings, get_review_queue, query_records


HEADER_FILL = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
HEADER_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name="Microsoft YaHei", size=9.5, color="1F2937")
LINK_FONT = Font(name="Microsoft YaHei", size=9.5, color="1D4ED8", underline="single")
ZEBRA_FILL = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

STATUS_STYLES = {
    "存续中": (
        PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
        Font(name="Microsoft YaHei", size=9.5, bold=True, color="166534"),
    ),
    "已到期": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Microsoft YaHei", size=9.5, bold=True, color="991B1B"),
    ),
    "已到期赎回": (
        PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
        Font(name="Microsoft YaHei", size=9.5, bold=True, color="92400E"),
    ),
    "随时可转让/赎回": (
        PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
        Font(name="Microsoft YaHei", size=9.5, bold=True, color="1E40AF"),
    ),
}

THIN_BORDER = Border(
    left=Side(style="thin", color="E2E8F0"),
    right=Side(style="thin", color="E2E8F0"),
    top=Side(style="thin", color="E2E8F0"),
    bottom=Side(style="thin", color="E2E8F0"),
)


DETAIL_COLUMNS = [
    ("上市公司名称", "company_name", 28, "left"),
    ("股票代码", "sec_code", 10, "center"),
    ("证券简称", "sec_name", 12, "center"),
    ("所在省份", "province", 10, "center"),
    ("所在城市", "city", 12, "center"),
    ("市场", "market", 8, "center"),
    ("交易类型", "record_type", 11, "center"),
    ("当前状态", "maturity_status", 15, "center"),
    ("机构类型", "institution_type", 11, "center"),
    ("受托机构(银行/券商)", "institution_main", 22, "left"),
    ("分支行/营业部", "branch_name", 20, "left"),
    ("产品名称", "product_name", 30, "left"),
    ("产品类型", "product_type", 14, "center"),
    ("金额(万元)", "amount_wan", 14, "right"),
    ("币种", "currency", 8, "center"),
    ("收益率区间", "yield_rate_raw", 15, "center"),
    ("最低收益率(%)", "yield_min", 13, "right"),
    ("最高收益率(%)", "yield_max", 13, "right"),
    ("开始日期(起息日)", "start_date", 14, "center"),
    ("到期日期", "end_date", 16, "center"),
    ("产品期限", "term_raw", 12, "center"),
    ("实际收益(万元)", "actual_income_wan", 14, "right"),
    ("资金来源", "fund_source", 14, "center"),
    ("原公告委托方(含子公司)", "entrusting_party", 24, "left"),
    ("公告日期", "announce_date", 12, "center"),
    ("原公告PDF链接", "pdf_url", 16, "center"),
]


def _style_header(ws, headers: List[tuple]) -> None:
    ws.row_dimensions[1].height = 28
    for idx, col in enumerate(headers, start=1):
        title, width = col[0], col[2]
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _write_detail_sheet(ws, items: List[Dict[str, Any]]) -> None:
    _style_header(ws, DETAIL_COLUMNS)
    for r_idx, item in enumerate(items, start=2):
        ws.row_dimensions[r_idx].height = 21
        is_zebra = (r_idx % 2 == 1)
        for c_idx, (_, key, _, align) in enumerate(DETAIL_COLUMNS, start=1):
            val = item.get(key)
            cell = ws.cell(row=r_idx, column=c_idx)
            if key == "pdf_url" and val:
                cell.value = "查看原文PDF"
                cell.hyperlink = val
                cell.font = LINK_FONT
            else:
                cell.value = val
                cell.font = BODY_FONT

            if key in ("amount_wan", "actual_income_wan") and val is not None:
                cell.number_format = "#,##0.00"
            elif key in ("yield_min", "yield_max") and val is not None:
                cell.number_format = "0.00"

            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.border = THIN_BORDER

            if key == "maturity_status" and val in STATUS_STYLES:
                fill, font = STATUS_STYLES[val]
                cell.fill = fill
                cell.font = font
            elif is_zebra:
                cell.fill = ZEBRA_FILL

    ws.auto_filter.ref = f"A1:{get_column_letter(len(DETAIL_COLUMNS))}{max(1, len(items) + 1)}"


def build_excel_workbook(
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "announce_date",
    sort_dir: str = "desc",
    include_extra_sheets: bool = True,
    db_path: Path = DB_PATH,
) -> Workbook:
    wb = Workbook()
    ws_main = wb.active
    ws_main.title = "现金管理明细总表"

    res = query_records(
        filters=filters or {},
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=1,
        page_size=0,  # 全量匹配记录
        db_path=db_path,
    )
    _write_detail_sheet(ws_main, res["items"])

    if include_extra_sheets:
        # Sheet 2: 未来30天即将到期（仅存续中）
        ws_exp = wb.create_sheet("未来30天即将到期")
        exp_res = query_records(
            filters={"maturity_status": "即将30天到期"},
            sort_by="end_date_iso",
            sort_dir="asc",
            page=1,
            page_size=0,
            db_path=db_path,
        )
        _write_detail_sheet(ws_exp, exp_res["items"])

        # Sheet 3: 银行与券商排行榜（本次新购口径）
        rankings = get_rankings(record_type_filter="本次新购", limit=100, db_path=db_path)
        ws_inst = wb.create_sheet("银行与券商吸金排行")
        inst_cols = [
            ("排名", "_rank", 8, "center"),
            ("机构类型", "institution_type", 13, "center"),
            ("主机构名称(银行/券商)", "institution_main", 26, "left"),
            ("新购总金额(万元)", "total_amount_wan", 18, "right"),
            ("新购总金额(亿元)", "_amount_yi", 16, "right"),
            ("产品笔数", "deal_count", 11, "right"),
            ("合作上市公司数", "company_count", 15, "right"),
            ("平均下限收益率(%)", "avg_yield_min", 17, "right"),
            ("平均上限收益率(%)", "avg_yield_max", 17, "right"),
        ]
        _style_header(ws_inst, inst_cols)
        for idx, row in enumerate(rankings["by_institution"], start=2):
            ws_inst.row_dimensions[idx].height = 20
            row_ext = dict(row)
            row_ext["_rank"] = idx - 1
            row_ext["_amount_yi"] = round((row["total_amount_wan"] or 0) / 10000.0, 2)
            for c_idx, (_, key, _, align) in enumerate(inst_cols, start=1):
                c = ws_inst.cell(row=idx, column=c_idx, value=row_ext.get(key))
                c.font = BODY_FONT
                c.alignment = Alignment(horizontal=align, vertical="center")
                c.border = THIN_BORDER
                if key in ("total_amount_wan", "_amount_yi"):
                    c.number_format = "#,##0.00"
                elif key in ("avg_yield_min", "avg_yield_max") and row_ext.get(key) is not None:
                    c.number_format = "0.00"
                if idx % 2 == 1:
                    c.fill = ZEBRA_FILL
        ws_inst.auto_filter.ref = f"A1:{get_column_letter(len(inst_cols))}{max(1, len(rankings['by_institution']) + 1)}"

        # Sheet 4: 上市公司现金管理排行
        ws_comp = wb.create_sheet("上市公司购买排行")
        comp_cols = [
            ("排名", "_rank", 8, "center"),
            ("股票代码", "sec_code", 11, "center"),
            ("证券简称", "sec_name", 13, "center"),
            ("上市公司全称", "company_name", 30, "left"),
            ("所在省份", "province", 10, "center"),
            ("所在城市", "city", 12, "center"),
            ("市场", "market", 9, "center"),
            ("新购总金额(万元)", "total_amount_wan", 18, "right"),
            ("新购总金额(亿元)", "_amount_yi", 16, "right"),
            ("购买笔数", "deal_count", 11, "right"),
            ("合作机构数", "inst_count", 12, "right"),
            ("平均上限收益率(%)", "avg_yield_max", 17, "right"),
        ]
        _style_header(ws_comp, comp_cols)
        for idx, row in enumerate(rankings["by_company"], start=2):
            ws_comp.row_dimensions[idx].height = 20
            row_ext = dict(row)
            row_ext["_rank"] = idx - 1
            row_ext["_amount_yi"] = round((row["total_amount_wan"] or 0) / 10000.0, 2)
            for c_idx, (_, key, _, align) in enumerate(comp_cols, start=1):
                c = ws_comp.cell(row=idx, column=c_idx, value=row_ext.get(key))
                c.font = BODY_FONT
                c.alignment = Alignment(horizontal=align, vertical="center")
                c.border = THIN_BORDER
                if key in ("total_amount_wan", "_amount_yi"):
                    c.number_format = "#,##0.00"
                elif key == "avg_yield_max" and row_ext.get(key) is not None:
                    c.number_format = "0.00"
                if idx % 2 == 1:
                    c.fill = ZEBRA_FILL
        ws_comp.auto_filter.ref = f"A1:{get_column_letter(len(comp_cols))}{max(1, len(rankings['by_company']) + 1)}"

        # Sheet 5: 待人工复核公告清单
        rev = get_review_queue(page=1, page_size=5000, db_path=db_path)
        ws_rev = wb.create_sheet("待复核公告清单")
        rev_cols = [
            ("公告日期", "announce_date", 12, "center"),
            ("股票代码", "sec_code", 10, "center"),
            ("证券简称", "sec_name", 12, "center"),
            ("上市公司名称", "company_name", 26, "left"),
            ("市场", "market", 8, "center"),
            ("公告标题", "title", 48, "left"),
            ("状态", "parse_status", 13, "center"),
            ("原因说明", "parse_note", 30, "left"),
            ("原公告PDF链接", "pdf_url", 16, "center"),
        ]
        _style_header(ws_rev, rev_cols)
        for idx, row in enumerate(rev["items"], start=2):
            ws_rev.row_dimensions[idx].height = 20
            for c_idx, (_, key, _, align) in enumerate(rev_cols, start=1):
                c = ws_rev.cell(row=idx, column=c_idx)
                val = row.get(key)
                if key == "pdf_url" and val:
                    c.value = "查看原文PDF"
                    c.hyperlink = val
                    c.font = LINK_FONT
                else:
                    c.value = val
                    c.font = BODY_FONT
                c.alignment = Alignment(horizontal=align, vertical="center")
                c.border = THIN_BORDER
                if idx % 2 == 1:
                    c.fill = ZEBRA_FILL
        ws_rev.auto_filter.ref = f"A1:{get_column_letter(len(rev_cols))}{max(1, len(rev['items']) + 1)}"

    return wb


def export_excel_to_file(output_path: Optional[Path] = None, db_path: Path = DB_PATH) -> Path:
    if output_path is None:
        today = date.today()
        today_tag = today.strftime("%Y%m%d")
        output_path = OUTPUT_DIR / f"A股现金管理{today.year}年汇总_{today_tag}.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = build_excel_workbook(include_extra_sheets=True, db_path=db_path)
    wb.save(output_path)
    return output_path


def export_excel_bytes(
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "announce_date",
    sort_dir: str = "desc",
    include_extra_sheets: bool = True,
    db_path: Path = DB_PATH,
) -> bytes:
    wb = build_excel_workbook(
        filters=filters,
        sort_by=sort_by,
        sort_dir=sort_dir,
        include_extra_sheets=include_extra_sheets,
        db_path=db_path,
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
