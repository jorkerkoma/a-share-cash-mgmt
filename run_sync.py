import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.exporter import export_excel_to_file
from app.sync_service import run_sync

BJ_TZ = timezone(timedelta(hours=8))


def main():
    parser = argparse.ArgumentParser(description="抓取A股现金管理/委托理财公告并导出Excel（PDF内存流式解析，不落盘）")
    parser.add_argument("--date-from", type=str, default=None, help="起始日期 YYYY-MM-DD（默认今年1月1日）")
    parser.add_argument("--date-to", type=str, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=None, help="便捷回溯天数（例如 --days 5 仅增量抓取最近5天）")
    parser.add_argument("--full", action="store_true", help="强制全量重跑（忽略已处理记录）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程/进程数（默认8）")
    parser.add_argument("--processes", action="store_true", help="启用多进程并发解析PDF（大跨度补抓提速3-4倍）")
    parser.add_argument("--no-excel", action="store_true", help="跳过自动导出Excel")
    args = parser.parse_args()

    today_bj = datetime.now(BJ_TZ).date()
    date_to = args.date_to or today_bj.strftime("%Y-%m-%d")
    if args.date_from:
        date_from = args.date_from
    elif args.days:
        date_from = (today_bj - timedelta(days=max(1, args.days))).strftime("%Y-%m-%d")
    else:
        date_from = None

    res = run_sync(
        date_from=date_from,
        date_to=date_to,
        incremental=not args.full,
        max_workers=args.workers,
        use_processes=args.processes,
        verbose=True,
    )
    if not args.no_excel:
        out = export_excel_to_file()
        print(f"[EXCEL] 已生成汇总表格: {out}", flush=True)
    return res


if __name__ == "__main__":
    main()
