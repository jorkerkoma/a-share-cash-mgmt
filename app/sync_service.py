import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.cninfo_client import create_cninfo_session, download_pdf_bytes, fetch_announcement_list
from app.config import DEFAULT_KEYWORDS, DEFAULT_LOOKBACK_DAYS, LLM_ENABLED, MAX_WORKERS
from app.db import (
    get_conn,
    get_existing_announcement_ids,
    init_db,
    refresh_maturity_statuses,
    replace_announcement_records,
    upsert_announcement,
)
from app.llm_fallback import llm_extract_records
from app.parser import parse_pdf_announcement


class SyncState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.phase = "idle"
        self.message = "空闲"
        self.total_found = 0
        self.to_process = 0
        self.processed = 0
        self.parsed_ok = 0
        self.quota_only = 0
        self.needs_review = 0
        self.skipped = 0
        self.errors = 0
        self.records_added = 0
        self.started_at = ""
        self.finished_at = ""

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "phase": self.phase,
                "message": self.message,
                "total_found": self.total_found,
                "to_process": self.to_process,
                "processed": self.processed,
                "parsed_ok": self.parsed_ok,
                "quota_only": self.quota_only,
                "needs_review": self.needs_review,
                "skipped": self.skipped,
                "errors": self.errors,
                "records_added": self.records_added,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)


SYNC_STATE = SyncState()


def _process_one_announcement(ann: Dict[str, Any], session) -> Dict[str, Any]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not ann.get("should_fetch"):
        status = "quota_only" if ann.get("ann_category") == "额度授权" else "skipped"
        return {
            "ann_row": {
                "announcement_id": ann["announcement_id"],
                "sec_code": ann["sec_code"],
                "sec_name": ann["sec_name"],
                "company_name": ann["sec_name"],
                "market": ann["market"],
                "title": ann["title"],
                "announce_date": ann["announce_date"],
                "pdf_url": ann["pdf_url"],
                "fund_source_hint": ann["fund_source_hint"],
                "ann_category": ann["ann_category"],
                "parse_status": status,
                "parse_note": "标题预过滤（非交易明细公告）",
                "record_count": 0,
                "parsed_at": now,
            },
            "records": [],
        }

    try:
        pdf_bytes = download_pdf_bytes(ann["adjunct_url"], session=session)
        llm_fn = llm_extract_records if LLM_ENABLED else None
        company_name, parse_status, parse_note, records = parse_pdf_announcement(
            ann, pdf_bytes, llm_fallback_fn=llm_fn
        )
        # 显式释放内存中的 PDF bytes
        del pdf_bytes
        return {
            "ann_row": {
                "announcement_id": ann["announcement_id"],
                "sec_code": ann["sec_code"],
                "sec_name": ann["sec_name"],
                "company_name": company_name,
                "market": ann["market"],
                "title": ann["title"],
                "announce_date": ann["announce_date"],
                "pdf_url": ann["pdf_url"],
                "fund_source_hint": ann["fund_source_hint"],
                "ann_category": ann["ann_category"],
                "parse_status": parse_status,
                "parse_note": parse_note,
                "record_count": len(records),
                "parsed_at": now,
            },
            "records": records,
        }
    except Exception as e:
        return {
            "ann_row": {
                "announcement_id": ann["announcement_id"],
                "sec_code": ann["sec_code"],
                "sec_name": ann["sec_name"],
                "company_name": ann["sec_name"],
                "market": ann["market"],
                "title": ann["title"],
                "announce_date": ann["announce_date"],
                "pdf_url": ann["pdf_url"],
                "fund_source_hint": ann["fund_source_hint"],
                "ann_category": ann["ann_category"],
                "parse_status": "error",
                "parse_note": f"处理异常: {str(e)[:180]}",
                "record_count": 0,
                "parsed_at": now,
            },
            "records": [],
        }


def run_sync(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    incremental: bool = True,
    max_workers: int = MAX_WORKERS,
    limit: Optional[int] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    init_db()
    if SYNC_STATE.running:
        return {"ok": False, "message": "已有同步任务正在运行"}

    today = date.today()
    date_to = date_to or today.strftime("%Y-%m-%d")
    date_from = date_from or (today - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    keywords = keywords or DEFAULT_KEYWORDS

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SYNC_STATE.update(
        running=True,
        phase="listing",
        message=f"正在从巨潮资讯网检索 {date_from} ~ {date_to} 公告目录...",
        total_found=0,
        to_process=0,
        processed=0,
        parsed_ok=0,
        quota_only=0,
        needs_review=0,
        skipped=0,
        errors=0,
        records_added=0,
        started_at=started_at,
        finished_at="",
    )

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sync_runs (started_at, date_from, date_to, status, message) VALUES (?, ?, ?, 'running', ?)",
            (started_at, date_from, date_to, "正在拉取公告列表"),
        )
        run_id = cur.lastrowid

    try:
        session = create_cninfo_session()

        def on_list_progress(msg: str):
            SYNC_STATE.update(message=msg)
            if verbose:
                print(f"[LIST] {msg}", flush=True)

        all_anns = fetch_announcement_list(
            date_from=date_from,
            date_to=date_to,
            keywords=keywords,
            session=session,
            progress_cb=on_list_progress,
        )
        SYNC_STATE.update(total_found=len(all_anns))

        if incremental:
            existing = get_existing_announcement_ids(include_errors=True)
            pending = [a for a in all_anns if a["announcement_id"] not in existing]
        else:
            pending = all_anns

        if limit:
            pending = pending[:limit]

        # 先批量落库已预过滤的公告，减少线程池开销
        to_fetch = [a for a in pending if a.get("should_fetch")]
        pre_skipped = [a for a in pending if not a.get("should_fetch")]

        SYNC_STATE.update(
            phase="parsing",
            to_process=len(pending),
            message=f"共发现 {len(all_anns)} 份公告，待处理 {len(pending)} 份（需在线解析 PDF {len(to_fetch)} 份，标题预过滤 {len(pre_skipped)} 份）",
        )
        if verbose:
            print(f"[SYNC] {SYNC_STATE.message}", flush=True)

        processed = 0
        parsed_ok = 0
        quota_only = 0
        needs_review = 0
        skipped = 0
        errors = 0
        records_added = 0

        # 写入预过滤的公告
        if pre_skipped:
            with get_conn() as conn:
                for a in pre_skipped:
                    res = _process_one_announcement(a, session)
                    ann_row = res["ann_row"]
                    upsert_announcement(conn, ann_row)
                    processed += 1
                    if ann_row["parse_status"] == "quota_only":
                        quota_only += 1
                    else:
                        skipped += 1
            SYNC_STATE.update(processed=processed, quota_only=quota_only, skipped=skipped)

        # 并发流式拉取并解析 PDF（每批落盘到 SQLite，不存任何 PDF 原文件）
        batch_buffer: List[Dict[str, Any]] = []

        def flush_batch(buf: List[Dict[str, Any]]):
            nonlocal records_added
            if not buf:
                return
            with get_conn() as conn:
                for item in buf:
                    ann_row = item["ann_row"]
                    recs = item["records"]
                    upsert_announcement(conn, ann_row)
                    if recs:
                        added = replace_announcement_records(conn, ann_row["announcement_id"], recs)
                        records_added += added
                    elif ann_row["parse_status"] in ("quota_only", "skipped"):
                        replace_announcement_records(conn, ann_row["announcement_id"], [])
            buf.clear()

        if to_fetch:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_process_one_announcement, a, session): a for a in to_fetch}
                for fut in as_completed(futures):
                    res = fut.result()
                    ann_row = res["ann_row"]
                    st = ann_row["parse_status"]
                    processed += 1
                    if st == "parsed":
                        parsed_ok += 1
                    elif st == "quota_only":
                        quota_only += 1
                    elif st == "needs_review":
                        needs_review += 1
                    elif st == "skipped":
                        skipped += 1
                    else:
                        errors += 1

                    batch_buffer.append(res)
                    if len(batch_buffer) >= 20:
                        flush_batch(batch_buffer)
                        SYNC_STATE.update(
                            processed=processed,
                            parsed_ok=parsed_ok,
                            quota_only=quota_only,
                            needs_review=needs_review,
                            skipped=skipped,
                            errors=errors,
                            records_added=records_added,
                            message=f"正在在线流式解析 PDF：{processed}/{len(pending)}（成功提取 {parsed_ok} 份 / 新增 {records_added} 条明细 / 待复核 {needs_review}）",
                        )
                        if verbose:
                            print(f"[PARSE] {SYNC_STATE.message}", flush=True)

            flush_batch(batch_buffer)

        # 刷新全部记录的到期状态（日期判定 + 赎回对账）及上市公司省市区域
        SYNC_STATE.update(phase="reconciling", message="正在自动核对到期状态并补齐上市公司省市区域...")
        refresh_maturity_statuses()
        try:
            from app.region_service import enrich_company_regions
            enrich_company_regions()
        except Exception:
            pass

        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_msg = (
            f"同步完成！发现 {len(all_anns)} 份，处理 {processed} 份："
            f"成功提取 {parsed_ok} 份（写入 {records_added} 条明细），"
            f"额度授权 {quota_only} 份，预过滤 {skipped} 份，待复核 {needs_review} 份，异常 {errors} 份。"
        )
        SYNC_STATE.update(
            running=False,
            phase="done",
            processed=processed,
            parsed_ok=parsed_ok,
            quota_only=quota_only,
            needs_review=needs_review,
            skipped=skipped,
            errors=errors,
            records_added=records_added,
            finished_at=finished_at,
            message=final_msg,
        )
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE sync_runs SET
                    finished_at=?, total_found=?, processed=?, parsed_ok=?,
                    quota_only=?, needs_review=?, skipped=?, errors=?,
                    records_added=?, status='done', message=?
                WHERE id=?
                """,
                (
                    finished_at,
                    len(all_anns),
                    processed,
                    parsed_ok,
                    quota_only,
                    needs_review,
                    skipped,
                    errors,
                    records_added,
                    final_msg,
                    run_id,
                ),
            )
        if verbose:
            print(f"[DONE] {final_msg}", flush=True)
        return {"ok": True, **SYNC_STATE.snapshot()}

    except Exception as e:
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        err_msg = f"同步中断：{e}"
        SYNC_STATE.update(running=False, phase="error", finished_at=finished_at, message=err_msg)
        with get_conn() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at=?, status='error', message=? WHERE id=?",
                (finished_at, err_msg, run_id),
            )
        raise
