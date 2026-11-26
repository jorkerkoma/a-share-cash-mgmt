import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import BASE_DIR, DB_PATH, WEB_DIR
from app.db import get_conn, get_overview_stats, get_review_queue, init_db, refresh_maturity_statuses
from app.exporter import export_excel_to_file

BJ_TZ = timezone(timedelta(hours=8))
DIST_DIR = BASE_DIR / "dist"
DIST_DATA_DIR = DIST_DIR / "data"

COMPACT_FIELDS = [
    "sec_code",
    "sec_name",
    "company_name",
    "market",
    "province",
    "city",
    "announce_date",
    "entrusting_party",
    "record_type",
    "maturity_status",
    "institution_type",
    "institution_main",
    "branch_name",
    "product_name",
    "product_type",
    "product_type_raw",
    "amount_wan",
    "currency",
    "yield_rate_raw",
    "yield_min",
    "yield_max",
    "actual_income_wan",
    "start_date",
    "end_date",
    "end_date_iso",
    "term_raw",
    "fund_source",
    "pdf_url",
]


def build_static_site(db_path: Path = DB_PATH) -> dict:
    init_db(db_path)
    bj_now = datetime.now(BJ_TZ)
    bj_today = bj_now.strftime("%Y-%m-%d")
    refresh_maturity_statuses(db_path=db_path, today_str=bj_today)

    DIST_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 导出紧凑数组格式的 records.json（体积压缩约55%，国内4G/5G秒开）
    cols_sql = ", ".join(COMPACT_FIELDS)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT {cols_sql} FROM records ORDER BY announce_date DESC, id DESC"
        ).fetchall()

    compact_rows = []
    for r in rows:
        compact_rows.append([r[f] for f in COMPACT_FIELDS])

    records_payload = {
        "generated_at": bj_now.strftime("%Y-%m-%d %H:%M"),
        "today": bj_today,
        "fields": COMPACT_FIELDS,
        "rows": compact_rows,
    }
    records_path = DIST_DATA_DIR / "records.json"
    records_path.write_text(
        json.dumps(records_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 2. 导出 meta.json（公告解析统计、上次同步时间、待复核清单）
    ov = get_overview_stats(db_path=db_path)
    rev = get_review_queue(page=1, page_size=2000, db_path=db_path)
    meta_payload = {
        "generated_at": bj_now.strftime("%Y-%m-%d %H:%M"),
        "today": bj_today,
        "announcements": ov["announcements"],
        "last_sync": ov["last_sync"],
        "review_items": [
            {
                "announce_date": x["announce_date"],
                "sec_code": x["sec_code"],
                "sec_name": x["sec_name"],
                "company_name": x["company_name"] or x["sec_name"],
                "market": x["market"],
                "title": x["title"],
                "parse_status": x["parse_status"],
                "parse_note": x["parse_note"],
                "pdf_url": x["pdf_url"],
            }
            for x in rev["items"]
        ],
    }
    meta_path = DIST_DATA_DIR / "meta.json"
    meta_path.write_text(
        json.dumps(meta_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 3. 同步生成全量5页样式化 Excel 供云端直接下载（仅保留单份 all.xlsx 减少仓库体积）
    xlsx_path = DIST_DATA_DIR / "all.xlsx"
    export_excel_to_file(output_path=xlsx_path, db_path=db_path)
    old_cn_xlsx = DIST_DATA_DIR / "A股现金管理汇总.xlsx"
    if old_cn_xlsx.exists():
        old_cn_xlsx.unlink()

    # 4. 生成静态版 dist/index.html（注入静态模式标记 + 防缓存版本号）
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    ver = bj_now.strftime("%Y%m%d%H%M")
    inject = f'<script>window.__STATIC_MODE__ = true; window.__DATA_VER__ = "{ver}";</script>\n</head>'
    if "</head>" in html:
        html = html.replace("</head>", inject, 1)
    (DIST_DIR / "index.html").write_text(html, encoding="utf-8")

    # 5. 生成 .nojekyll（兼容 GitHub Pages / EdgeOne Pages / Cloudflare Pages）
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")

    return {
        "dist_dir": str(DIST_DIR),
        "records_count": len(compact_rows),
        "records_json_kb": round(records_path.stat().st_size / 1024, 1),
        "meta_json_kb": round(meta_path.stat().st_size / 1024, 1),
        "excel_kb": round(xlsx_path.stat().st_size / 1024, 1),
        "generated_at": records_payload["generated_at"],
    }


if __name__ == "__main__":
    info = build_static_site()
    print("[STATIC BUILD OK]", info, flush=True)
