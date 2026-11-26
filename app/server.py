import threading
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app.config import WEB_DIR
from app.db import (
    get_filter_options,
    get_overview_stats,
    get_rankings,
    get_review_queue,
    init_db,
    query_records,
    refresh_maturity_statuses,
)
from app.exporter import export_excel_bytes, export_excel_to_file
from app.sync_service import SYNC_STATE, run_sync

app = FastAPI(title="A股上市公司现金管理监测平台", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    refresh_maturity_statuses()


@app.get("/")
def serve_index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/overview")
def api_overview():
    return get_overview_stats()


@app.get("/api/filters")
def api_filters(
    q: str = "",
    market: str = "",
    province: str = "",
    city: str = "",
    record_type: str = "",
    maturity_status: str = "",
    institution_type: str = "",
    institution_main: str = "",
    branch_name: str = "",
    product_type: str = "",
    fund_source: str = "",
    sec_code: str = "",
    min_amount: Optional[str] = None,
    max_amount: Optional[str] = None,
    min_yield: Optional[str] = None,
    max_yield: Optional[str] = None,
    ann_from: str = "",
    ann_to: str = "",
    start_from: str = "",
    start_to: str = "",
    end_from: str = "",
    end_to: str = "",
):
    filters = {
        "q": q,
        "market": market,
        "province": province,
        "city": city,
        "record_type": record_type,
        "maturity_status": maturity_status,
        "institution_type": institution_type,
        "institution_main": institution_main,
        "branch_name": branch_name,
        "product_type": product_type,
        "fund_source": fund_source,
        "sec_code": sec_code,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "min_yield": min_yield,
        "max_yield": max_yield,
        "ann_from": ann_from,
        "ann_to": ann_to,
        "start_from": start_from,
        "start_to": start_to,
        "end_from": end_from,
        "end_to": end_to,
    }
    return get_filter_options(filters=filters)


@app.get("/api/records")
def api_records(
    q: str = "",
    market: str = "",
    province: str = "",
    city: str = "",
    record_type: str = "",
    maturity_status: str = "",
    institution_type: str = "",
    institution_main: str = "",
    branch_name: str = "",
    product_type: str = "",
    fund_source: str = "",
    sec_code: str = "",
    min_amount: Optional[str] = None,
    max_amount: Optional[str] = None,
    min_yield: Optional[str] = None,
    max_yield: Optional[str] = None,
    ann_from: str = "",
    ann_to: str = "",
    start_from: str = "",
    start_to: str = "",
    end_from: str = "",
    end_to: str = "",
    sort_by: str = "announce_date",
    sort_dir: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    filters = {
        "q": q,
        "market": market,
        "province": province,
        "city": city,
        "record_type": record_type,
        "maturity_status": maturity_status,
        "institution_type": institution_type,
        "institution_main": institution_main,
        "branch_name": branch_name,
        "product_type": product_type,
        "fund_source": fund_source,
        "sec_code": sec_code,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "min_yield": min_yield,
        "max_yield": max_yield,
        "ann_from": ann_from,
        "ann_to": ann_to,
        "start_from": start_from,
        "start_to": start_to,
        "end_from": end_from,
        "end_to": end_to,
    }
    return query_records(filters=filters, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)


@app.get("/api/rankings")
def api_rankings(
    record_type: str = "本次新购",
    institution_type: str = "",
    limit: int = Query(40, ge=5, le=200),
):
    return get_rankings(record_type_filter=record_type, inst_type_filter=institution_type, limit=limit)


@app.get("/api/review")
def api_review(page: int = Query(1, ge=1), page_size: int = Query(50, ge=10, le=200)):
    return get_review_queue(page=page, page_size=page_size)


@app.get("/api/export")
def api_export(
    q: str = "",
    market: str = "",
    province: str = "",
    city: str = "",
    record_type: str = "",
    maturity_status: str = "",
    institution_type: str = "",
    institution_main: str = "",
    branch_name: str = "",
    product_type: str = "",
    fund_source: str = "",
    sec_code: str = "",
    min_amount: Optional[str] = None,
    max_amount: Optional[str] = None,
    min_yield: Optional[str] = None,
    max_yield: Optional[str] = None,
    ann_from: str = "",
    ann_to: str = "",
    start_from: str = "",
    start_to: str = "",
    end_from: str = "",
    end_to: str = "",
    sort_by: str = "announce_date",
    sort_dir: str = "desc",
    full: bool = False,
):
    filters = {} if full else {
        "q": q,
        "market": market,
        "province": province,
        "city": city,
        "record_type": record_type,
        "maturity_status": maturity_status,
        "institution_type": institution_type,
        "institution_main": institution_main,
        "branch_name": branch_name,
        "product_type": product_type,
        "fund_source": fund_source,
        "sec_code": sec_code,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "min_yield": min_yield,
        "max_yield": max_yield,
        "ann_from": ann_from,
        "ann_to": ann_to,
        "start_from": start_from,
        "start_to": start_to,
        "end_from": end_from,
        "end_to": end_to,
    }
    has_any_filter = any(v is not None and str(v).strip() != "" for v in filters.values())
    data = export_excel_bytes(
        filters=filters,
        sort_by=sort_by,
        sort_dir=sort_dir,
        include_extra_sheets=not has_any_filter,
    )
    tag = "全量汇总" if not has_any_filter else "筛选导出"
    filename = f"A股现金管理_{tag}_{date.today().strftime('%Y%m%d')}.xlsx"
    encoded = quote(filename)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


class SyncRequest(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    incremental: bool = True


@app.post("/api/sync")
def api_start_sync(req: SyncRequest):
    if SYNC_STATE.running:
        return JSONResponse({"ok": False, "message": "已有同步任务正在运行", "state": SYNC_STATE.snapshot()}, status_code=409)

    def _worker():
        try:
            run_sync(
                date_from=req.date_from or None,
                date_to=req.date_to or None,
                incremental=req.incremental,
                verbose=False,
            )
            export_excel_to_file()
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"ok": True, "message": "已启动后台同步任务", "state": SYNC_STATE.snapshot()}


@app.get("/api/sync/status")
def api_sync_status():
    return SYNC_STATE.snapshot()
