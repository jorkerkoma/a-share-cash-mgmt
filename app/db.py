import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import DB_PATH


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                announcement_id TEXT PRIMARY KEY,
                sec_code TEXT NOT NULL,
                sec_name TEXT NOT NULL,
                company_name TEXT,
                market TEXT,
                title TEXT NOT NULL,
                announce_date TEXT NOT NULL,
                pdf_url TEXT NOT NULL,
                fund_source_hint TEXT,
                ann_category TEXT,
                parse_status TEXT NOT NULL DEFAULT 'pending',
                parse_note TEXT,
                record_count INTEGER NOT NULL DEFAULT 0,
                parsed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                announcement_id TEXT NOT NULL,
                sec_code TEXT NOT NULL,
                sec_name TEXT NOT NULL,
                company_name TEXT NOT NULL,
                market TEXT,
                announce_date TEXT NOT NULL,
                entrusting_party TEXT,
                record_type TEXT NOT NULL,
                maturity_status TEXT NOT NULL,
                institution_raw TEXT,
                institution_type TEXT,
                institution_main TEXT,
                branch_name TEXT,
                product_name TEXT,
                product_type TEXT,
                product_type_raw TEXT,
                amount_wan REAL,
                amount_raw TEXT,
                currency TEXT DEFAULT '人民币',
                yield_rate_raw TEXT,
                yield_min REAL,
                yield_max REAL,
                actual_income_wan REAL,
                actual_yield_pct REAL,
                start_date TEXT,
                end_date TEXT,
                end_date_iso TEXT,
                term_raw TEXT,
                fund_source TEXT,
                section_title TEXT,
                pdf_url TEXT NOT NULL,
                ann_title TEXT,
                parse_method TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (announcement_id) REFERENCES announcements(announcement_id)
            );

            CREATE TABLE IF NOT EXISTS companies (
                sec_code TEXT PRIMARY KEY,
                province TEXT,
                city TEXT,
                reg_address TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                date_from TEXT,
                date_to TEXT,
                total_found INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                parsed_ok INTEGER DEFAULT 0,
                quota_only INTEGER DEFAULT 0,
                needs_review INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                records_added INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_records_sec_code ON records(sec_code);
            CREATE INDEX IF NOT EXISTS idx_records_ann_date ON records(announce_date);
            CREATE INDEX IF NOT EXISTS idx_records_inst_main ON records(institution_main);
            CREATE INDEX IF NOT EXISTS idx_records_inst_type ON records(institution_type);
            CREATE INDEX IF NOT EXISTS idx_records_status ON records(maturity_status);
            CREATE INDEX IF NOT EXISTS idx_records_rec_type ON records(record_type);
            CREATE INDEX IF NOT EXISTS idx_records_end_iso ON records(end_date_iso);
            CREATE INDEX IF NOT EXISTS idx_records_fund_src ON records(fund_source);
            CREATE INDEX IF NOT EXISTS idx_records_ann_id ON records(announcement_id);
            CREATE INDEX IF NOT EXISTS idx_ann_status ON announcements(parse_status);
            CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announce_date);
            """
        )
        # 安全增量迁移：为旧库自动补加 province / city 列及索引
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
        if "province" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN province TEXT DEFAULT ''")
        if "city" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN city TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_province ON records(province)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_city ON records(city)")


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_existing_announcement_ids(db_path: Path = DB_PATH, include_errors: bool = False) -> set:
    with get_conn(db_path) as conn:
        if include_errors:
            rows = conn.execute(
                "SELECT announcement_id FROM announcements WHERE parse_status IN ('parsed','quota_only','skipped','needs_review')"
            ).fetchall()
        else:
            rows = conn.execute("SELECT announcement_id FROM announcements").fetchall()
        return {r["announcement_id"] for r in rows}


def upsert_announcement(conn: sqlite3.Connection, ann: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO announcements (
            announcement_id, sec_code, sec_name, company_name, market, title,
            announce_date, pdf_url, fund_source_hint, ann_category,
            parse_status, parse_note, record_count, parsed_at
        ) VALUES (
            :announcement_id, :sec_code, :sec_name, :company_name, :market, :title,
            :announce_date, :pdf_url, :fund_source_hint, :ann_category,
            :parse_status, :parse_note, :record_count, :parsed_at
        )
        ON CONFLICT(announcement_id) DO UPDATE SET
            company_name=excluded.company_name,
            fund_source_hint=excluded.fund_source_hint,
            ann_category=excluded.ann_category,
            parse_status=excluded.parse_status,
            parse_note=excluded.parse_note,
            record_count=excluded.record_count,
            parsed_at=excluded.parsed_at
        """,
        ann,
    )


def replace_announcement_records(
    conn: sqlite3.Connection, announcement_id: str, records: List[Dict[str, Any]]
) -> int:
    conn.execute("DELETE FROM records WHERE announcement_id = ?", (announcement_id,))
    inserted = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for rec in records:
        rec = dict(rec)
        rec.setdefault("created_at", now)
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO records (
                    fingerprint, announcement_id, sec_code, sec_name, company_name,
                    market, announce_date, entrusting_party, record_type, maturity_status,
                    institution_raw, institution_type, institution_main, branch_name,
                    product_name, product_type, product_type_raw,
                    amount_wan, amount_raw, currency,
                    yield_rate_raw, yield_min, yield_max,
                    actual_income_wan, actual_yield_pct,
                    start_date, end_date, end_date_iso, term_raw,
                    fund_source, section_title, pdf_url, ann_title, parse_method, created_at
                ) VALUES (
                    :fingerprint, :announcement_id, :sec_code, :sec_name, :company_name,
                    :market, :announce_date, :entrusting_party, :record_type, :maturity_status,
                    :institution_raw, :institution_type, :institution_main, :branch_name,
                    :product_name, :product_type, :product_type_raw,
                    :amount_wan, :amount_raw, :currency,
                    :yield_rate_raw, :yield_min, :yield_max,
                    :actual_income_wan, :actual_yield_pct,
                    :start_date, :end_date, :end_date_iso, :term_raw,
                    :fund_source, :section_title, :pdf_url, :ann_title, :parse_method, :created_at
                )
                """,
                rec,
            )
            inserted += cur.rowcount
        except sqlite3.Error:
            pass
    return inserted


def refresh_maturity_statuses(db_path: Path = DB_PATH, today_str: Optional[str] = None) -> None:
    """
    1. 根据当前日期自动刷新存续中 -> 已到期
    2. 如果某笔“本次新购”在同一上市公司、主机构、金额接近且起息日/产品匹配的前提下
       已出现对应的“到期赎回”记录，则标记为“已到期”
    """
    today_str = today_str or date.today().strftime("%Y-%m-%d")
    with get_conn(db_path) as conn:
        # Step 1: Date-based maturity refresh
        conn.execute(
            """
            UPDATE records
            SET maturity_status = '已到期'
            WHERE record_type = '本次新购'
              AND maturity_status IN ('存续中', '未知')
              AND end_date_iso IS NOT NULL
              AND end_date_iso != ''
              AND end_date_iso <= ?
            """,
            (today_str,),
        )
        conn.execute(
            """
            UPDATE records
            SET maturity_status = '存续中'
            WHERE record_type = '本次新购'
              AND maturity_status = '已到期'
              AND end_date_iso IS NOT NULL
              AND end_date_iso != ''
              AND end_date_iso > ?
            """,
            (today_str,),
        )
        # Step 2: Cross-match redeemed records to mark matching purchase records as 已到期
        # 严格校验：
        # 1) 赎回公告必须严格晚于购买公告（r2.announce_date > records.announce_date），杜绝“同一份到期赎回并继续购买公告”把刚新买的2027年产品误判为已到期
        # 2) 若双方均有起息日，必须起息日完全一致且到期日不冲突
        # 3) 若缺起息日，仅当产品名称含具体期号/编码数字（排除“人民币结构性存款产品”这类通用名）且到期日在赎回公告之前/附近才匹配
        conn.execute(
            """
            UPDATE records
            SET maturity_status = '已到期'
            WHERE record_type = '本次新购'
              AND maturity_status IN ('存续中', '随时可转让/赎回', '未知')
              AND EXISTS (
                  SELECT 1 FROM records r2
                  WHERE r2.record_type = '到期赎回'
                    AND r2.sec_code = records.sec_code
                    AND r2.institution_main = records.institution_main
                    AND r2.announce_date > records.announce_date
                    AND (
                        (
                            records.start_date IS NOT NULL AND records.start_date != ''
                            AND r2.start_date IS NOT NULL AND r2.start_date != ''
                            AND r2.start_date = records.start_date
                            AND (
                                records.end_date_iso IS NULL OR records.end_date_iso = ''
                                OR r2.end_date_iso IS NULL OR r2.end_date_iso = ''
                                OR r2.end_date_iso = records.end_date_iso
                            )
                        )
                        OR (
                            (records.start_date IS NULL OR records.start_date = '' OR r2.start_date IS NULL OR r2.start_date = '')
                            AND records.product_name IS NOT NULL AND LENGTH(records.product_name) >= 12
                            AND records.product_name GLOB '*[0-9][0-9][0-9]*'
                            AND r2.product_name = records.product_name
                            AND (
                                r2.end_date_iso IS NULL OR r2.end_date_iso = ''
                                OR r2.end_date_iso >= records.announce_date
                            )
                        )
                    )
                    AND (
                        records.amount_wan IS NULL OR r2.amount_wan IS NULL
                        OR ABS(records.amount_wan - r2.amount_wan) <= MAX(1.0, records.amount_wan * 0.02)
                    )
              )
            """
        )


def build_where_clause(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []

    q = (filters.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        clauses.append(
            "(company_name LIKE ? OR sec_name LIKE ? OR sec_code LIKE ? OR province LIKE ? OR city LIKE ? OR institution_main LIKE ? OR branch_name LIKE ? OR institution_raw LIKE ? OR product_name LIKE ?)"
        )
        params.extend([like] * 9)

    for col, key in [
        ("market", "market"),
        ("province", "province"),
        ("city", "city"),
        ("record_type", "record_type"),
        ("institution_type", "institution_type"),
        ("institution_main", "institution_main"),
        ("product_type", "product_type"),
        ("fund_source", "fund_source"),
        ("sec_code", "sec_code"),
    ]:
        val = (filters.get(key) or "").strip()
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)

    branch = (filters.get("branch_name") or "").strip()
    if branch:
        if branch == "__HAS_BRANCH__":
            clauses.append("branch_name IS NOT NULL AND branch_name != '' AND branch_name != '未披露'")
        else:
            clauses.append("branch_name LIKE ?")
            params.append(f"%{branch}%")

    status = (filters.get("maturity_status") or "").strip()
    if status:
        if status == "已到期合集":
            clauses.append("maturity_status IN ('已到期', '已到期赎回')")
        elif status == "即将30天到期":
            today_str = date.today().strftime("%Y-%m-%d")
            from datetime import timedelta
            plus30 = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
            clauses.append(
                "record_type = '本次新购' AND maturity_status = '存续中' AND end_date_iso >= ? AND end_date_iso <= ?"
            )
            params.extend([today_str, plus30])
        elif status == "即将7天到期":
            today_str = date.today().strftime("%Y-%m-%d")
            from datetime import timedelta
            plus7 = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
            clauses.append(
                "record_type = '本次新购' AND maturity_status = '存续中' AND end_date_iso >= ? AND end_date_iso <= ?"
            )
            params.extend([today_str, plus7])
        else:
            clauses.append("maturity_status = ?")
            params.append(status)

    if filters.get("min_amount") is not None and str(filters.get("min_amount")).strip() != "":
        clauses.append("amount_wan >= ?")
        params.append(float(filters["min_amount"]))
    if filters.get("max_amount") is not None and str(filters.get("max_amount")).strip() != "":
        clauses.append("amount_wan <= ?")
        params.append(float(filters["max_amount"]))

    if filters.get("min_yield") is not None and str(filters.get("min_yield")).strip() != "":
        clauses.append("COALESCE(yield_max, yield_min) >= ?")
        params.append(float(filters["min_yield"]))
    if filters.get("max_yield") is not None and str(filters.get("max_yield")).strip() != "":
        clauses.append("COALESCE(yield_min, yield_max) <= ?")
        params.append(float(filters["max_yield"]))

    for col, key, op in [
        ("announce_date", "ann_from", ">="),
        ("announce_date", "ann_to", "<="),
        ("start_date", "start_from", ">="),
        ("start_date", "start_to", "<="),
        ("end_date_iso", "end_from", ">="),
        ("end_date_iso", "end_to", "<="),
    ]:
        val = (filters.get(key) or "").strip()
        if val:
            clauses.append(f"{col} {op} ?")
            params.append(val)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


ALLOWED_SORT_COLS = {
    "announce_date": "announce_date",
    "amount_wan": "amount_wan",
    "yield_max": "COALESCE(yield_max, yield_min, -1)",
    "yield_min": "COALESCE(yield_min, yield_max, -1)",
    "start_date": "start_date",
    "end_date_iso": "COALESCE(NULLIF(end_date_iso,''), '9999-12-31')",
    "company_name": "company_name",
    "province": "province, city",
    "institution_main": "institution_main",
    "id": "id",
}


def query_records(
    filters: Dict[str, Any],
    sort_by: str = "announce_date",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    where_sql, params = build_where_clause(filters)
    sort_expr = ALLOWED_SORT_COLS.get(sort_by, "announce_date")
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    order_sql = f"ORDER BY {sort_expr} {direction}, id DESC"

    with get_conn(db_path) as conn:
        summary_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN currency = '人民币' OR currency IS NULL OR currency = '' THEN amount_wan ELSE 0 END), 0) AS total_amount_wan,
                COUNT(DISTINCT sec_code) AS unique_companies,
                COUNT(DISTINCT institution_main) AS unique_institutions,
                AVG(COALESCE(yield_max, yield_min)) AS avg_yield_max
            FROM records
            {where_sql}
            """,
            params,
        ).fetchone()

        offset = max(0, (page - 1) * page_size)
        if page_size > 0:
            rows = conn.execute(
                f"SELECT * FROM records {where_sql} {order_sql} LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM records {where_sql} {order_sql}",
                params,
            ).fetchall()

        return {
            "total": summary_row["total"],
            "total_amount_wan": round(summary_row["total_amount_wan"] or 0.0, 2),
            "unique_companies": summary_row["unique_companies"],
            "unique_institutions": summary_row["unique_institutions"],
            "avg_yield_max": round(summary_row["avg_yield_max"], 2) if summary_row["avg_yield_max"] is not None else None,
            "items": [dict(r) for r in rows],
        }


def get_overview_stats(db_path: Path = DB_PATH) -> Dict[str, Any]:
    today_str = date.today().strftime("%Y-%m-%d")
    from datetime import timedelta
    plus30 = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")

    with get_conn(db_path) as conn:
        rec_stats = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) AS total_records,
                    SUM(CASE WHEN record_type = '本次新购' THEN 1 ELSE 0 END) AS buy_records,
                    SUM(CASE WHEN record_type = '到期赎回' THEN 1 ELSE 0 END) AS redeem_records,
                    SUM(CASE WHEN maturity_status = '存续中' THEN 1 ELSE 0 END) AS active_records,
                    SUM(CASE WHEN maturity_status IN ('已到期', '已到期赎回') THEN 1 ELSE 0 END) AS matured_records,
                    SUM(CASE WHEN maturity_status = '随时可转让/赎回' THEN 1 ELSE 0 END) AS flexible_records,
                    SUM(CASE WHEN record_type = '本次新购' AND maturity_status = '存续中' AND end_date_iso >= ? AND end_date_iso <= ? THEN 1 ELSE 0 END) AS expiring_30d_count,
                    COALESCE(SUM(CASE WHEN record_type = '本次新购' AND maturity_status = '存续中' AND end_date_iso >= ? AND end_date_iso <= ? AND (currency='人民币' OR currency IS NULL) THEN amount_wan ELSE 0 END), 0) AS expiring_30d_amount_wan,
                    COALESCE(SUM(CASE WHEN record_type = '本次新购' AND (currency='人民币' OR currency IS NULL) THEN amount_wan ELSE 0 END), 0) AS total_buy_amount_wan,
                    COALESCE(SUM(CASE WHEN record_type = '到期赎回' AND (currency='人民币' OR currency IS NULL) THEN amount_wan ELSE 0 END), 0) AS total_redeem_amount_wan,
                    COUNT(DISTINCT sec_code) AS unique_companies,
                    COUNT(DISTINCT institution_main) AS unique_institutions,
                    MIN(announce_date) AS min_date,
                    MAX(announce_date) AS max_date
                FROM records
                """,
                (today_str, plus30, today_str, plus30),
            ).fetchone()
        )
        ann_stats = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) AS total_announcements,
                    SUM(CASE WHEN parse_status = 'parsed' THEN 1 ELSE 0 END) AS parsed_announcements,
                    SUM(CASE WHEN parse_status = 'quota_only' THEN 1 ELSE 0 END) AS quota_only_announcements,
                    SUM(CASE WHEN parse_status = 'needs_review' THEN 1 ELSE 0 END) AS needs_review_announcements,
                    SUM(CASE WHEN parse_status = 'skipped' THEN 1 ELSE 0 END) AS skipped_announcements,
                    SUM(CASE WHEN parse_status = 'error' THEN 1 ELSE 0 END) AS error_announcements
                FROM announcements
                """
            ).fetchone()
        )
        last_sync = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "records": rec_stats,
            "announcements": ann_stats,
            "last_sync": dict(last_sync) if last_sync else None,
        }


def get_rankings(
    record_type_filter: str = "本次新购",
    inst_type_filter: str = "",
    limit: int = 30,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    clauses = ["(currency = '人民币' OR currency IS NULL OR currency = '')"]
    params: List[Any] = []
    if record_type_filter and record_type_filter != "全部":
        clauses.append("record_type = ?")
        params.append(record_type_filter)
    if inst_type_filter:
        clauses.append("institution_type = ?")
        params.append(inst_type_filter)
    where_sql = "WHERE " + " AND ".join(clauses)

    with get_conn(db_path) as conn:
        by_institution = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    institution_main,
                    MAX(institution_type) AS institution_type,
                    COUNT(*) AS deal_count,
                    COUNT(DISTINCT sec_code) AS company_count,
                    ROUND(COALESCE(SUM(amount_wan), 0), 2) AS total_amount_wan,
                    ROUND(AVG(COALESCE(yield_max, yield_min)), 2) AS avg_yield_max,
                    ROUND(AVG(COALESCE(yield_min, yield_max)), 2) AS avg_yield_min
                FROM records
                {where_sql}
                  AND institution_main IS NOT NULL AND institution_main != ''
                GROUP BY institution_main
                ORDER BY total_amount_wan DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        ]

        by_company = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    sec_code,
                    MAX(sec_name) AS sec_name,
                    MAX(company_name) AS company_name,
                    MAX(market) AS market,
                    MAX(province) AS province,
                    MAX(city) AS city,
                    COUNT(*) AS deal_count,
                    COUNT(DISTINCT institution_main) AS inst_count,
                    ROUND(COALESCE(SUM(amount_wan), 0), 2) AS total_amount_wan,
                    ROUND(AVG(COALESCE(yield_max, yield_min)), 2) AS avg_yield_max
                FROM records
                {where_sql}
                GROUP BY sec_code
                ORDER BY total_amount_wan DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        ]

        by_product_type = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(product_type,''), '其他') AS product_type,
                    COUNT(*) AS deal_count,
                    ROUND(COALESCE(SUM(amount_wan), 0), 2) AS total_amount_wan,
                    ROUND(AVG(COALESCE(yield_min, yield_max)), 2) AS avg_yield_min,
                    ROUND(AVG(COALESCE(yield_max, yield_min)), 2) AS avg_yield_max
                FROM records
                {where_sql}
                GROUP BY COALESCE(NULLIF(product_type,''), '其他')
                ORDER BY total_amount_wan DESC
                """,
                params,
            ).fetchall()
        ]

        by_inst_type = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(institution_type,''), '其他') AS institution_type,
                    COUNT(*) AS deal_count,
                    ROUND(COALESCE(SUM(amount_wan), 0), 2) AS total_amount_wan,
                    ROUND(AVG(COALESCE(yield_max, yield_min)), 2) AS avg_yield_max
                FROM records
                {where_sql}
                GROUP BY COALESCE(NULLIF(institution_type,''), '其他')
                ORDER BY total_amount_wan DESC
                """,
                params,
            ).fetchall()
        ]

        by_branch = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    institution_main,
                    branch_name,
                    MAX(institution_type) AS institution_type,
                    COUNT(*) AS deal_count,
                    COUNT(DISTINCT sec_code) AS company_count,
                    ROUND(COALESCE(SUM(amount_wan), 0), 2) AS total_amount_wan,
                    ROUND(AVG(COALESCE(yield_max, yield_min)), 2) AS avg_yield_max
                FROM records
                {where_sql}
                  AND branch_name IS NOT NULL AND branch_name != '' AND branch_name != '未披露'
                GROUP BY institution_main, branch_name
                ORDER BY total_amount_wan DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
        ]

        return {
            "by_institution": by_institution,
            "by_company": by_company,
            "by_product_type": by_product_type,
            "by_inst_type": by_inst_type,
            "by_branch": by_branch,
        }


def get_filter_options(filters: Optional[Dict[str, Any]] = None, db_path: Path = DB_PATH) -> Dict[str, Any]:
    # 1. 主机构动态联动笔数（应用除 institution_main 本身之外的所有当前筛选条件）
    inst_filters = dict(filters or {})
    inst_filters.pop("institution_main", None)
    where_sql, params = build_where_clause(inst_filters)
    extra_cond = "institution_main IS NOT NULL AND institution_main != '' AND institution_main != '未披露' AND INSTR(institution_main, '%') = 0"
    inst_where = f"{where_sql} AND {extra_cond}" if where_sql else f"WHERE {extra_cond}"

    # 2. 省份动态联动笔数（应用除 province/city 之外的所有当前筛选条件）
    prov_filters = dict(filters or {})
    prov_filters.pop("province", None)
    prov_filters.pop("city", None)
    p_where_sql, p_params = build_where_clause(prov_filters)
    p_extra = "province IS NOT NULL AND province != '' AND province != '未知'"
    prov_where = f"{p_where_sql} AND {p_extra}" if p_where_sql else f"WHERE {p_extra}"

    # 3. 城市动态联动笔数（应用包含 province 在内、除 city 本身之外的所有当前筛选条件 -> 选省份后城市自动级联！）
    city_filters = dict(filters or {})
    city_filters.pop("city", None)
    c_where_sql, c_params = build_where_clause(city_filters)
    c_extra = "city IS NOT NULL AND city != '' AND city != '其他/未注明'"
    city_where = f"{c_where_sql} AND {c_extra}" if c_where_sql else f"WHERE {c_extra}"

    with get_conn(db_path) as conn:
        def distinct_list(sql: str) -> List[str]:
            return [r[0] for r in conn.execute(sql).fetchall() if r[0]]

        return {
            "markets": distinct_list("SELECT DISTINCT market FROM records WHERE market != '' ORDER BY market"),
            "institution_types": distinct_list("SELECT DISTINCT institution_type FROM records WHERE institution_type != '' ORDER BY institution_type"),
            "product_types": distinct_list("SELECT DISTINCT product_type FROM records WHERE product_type != '' ORDER BY product_type"),
            "fund_sources": distinct_list("SELECT DISTINCT fund_source FROM records WHERE fund_source != '' ORDER BY fund_source"),
            "provinces": [
                {"name": r[0], "count": r[1]}
                for r in conn.execute(
                    f"SELECT province, COUNT(*) c FROM records {prov_where} GROUP BY province ORDER BY c DESC",
                    p_params,
                ).fetchall()
            ],
            "cities": [
                {"name": r[0], "province": r[1], "count": r[2]}
                for r in conn.execute(
                    f"SELECT city, MAX(province), COUNT(*) c FROM records {city_where} GROUP BY city ORDER BY c DESC LIMIT 200",
                    c_params,
                ).fetchall()
            ],
            "top_institutions": [
                {"name": r[0], "count": r[1]}
                for r in conn.execute(
                    f"""
                    SELECT institution_main, COUNT(*) c
                    FROM records
                    {inst_where}
                    GROUP BY institution_main
                    ORDER BY c DESC
                    LIMIT 300
                    """,
                    params,
                ).fetchall()
            ],
        }


def get_review_queue(page: int = 1, page_size: int = 50, db_path: Path = DB_PATH) -> Dict[str, Any]:
    offset = max(0, (page - 1) * page_size)
    with get_conn(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE parse_status IN ('needs_review', 'error')"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM announcements
            WHERE parse_status IN ('needs_review', 'error')
            ORDER BY announce_date DESC, announcement_id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}
