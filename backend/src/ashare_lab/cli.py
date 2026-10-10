import argparse
import json
from datetime import date

import uvicorn

from ashare_lab.config import Settings
from ashare_lab.db import Database
from ashare_lab.fixtures import FIXTURE_DATE, load_fixture
from ashare_lab.schemas import ScreeningRequest, SyncRequest
from ashare_lab.services import ApplicationService
from ashare_lab.warehouse import MarketRepository
from ashare_lab.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="ashare-lab 数据与任务管理")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("fixture")
    sub.add_parser("statistics", help="补齐并预热当前不可变版本统计；可重复运行")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    sync = sub.add_parser("sync")
    sync.add_argument("--start")
    sync.add_argument("--end")
    sync.add_argument("--history-refresh", choices=["incremental", "full"])
    mode = sync.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true")
    mode.add_argument("--namechange", action="store_true")
    mode.add_argument("--dividend", action="store_true")
    screen = sub.add_parser("screen")
    screen.add_argument("--date", default=FIXTURE_DATE)
    screen.add_argument("--rule", choices=list("ABCDEF"), default="A")
    screen.add_argument("--parameters", default="{}")
    job = sub.add_parser("job")
    job.add_argument("id")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        uvicorn.run(
            "ashare_lab.api:create_app", factory=True, host=settings.api_host, port=args.port
        )
        return
    if args.command == "worker":
        Worker(settings).serve(once=args.once)
        return
    if args.command == "statistics":
        db = Database(settings.database_path)
        db.initialize()
        repository = MarketRepository(db, settings.data_dir)
        for dataset, version_id in sorted(db.snapshot().items()):
            value = repository.summary(version_id)
            print(
                json.dumps(
                    {"dataset": dataset, "version_id": version_id, **value}, ensure_ascii=False
                ),
                flush=True,
            )
        return
    service = ApplicationService(settings)
    if args.command == "init":
        print("SQLite WAL 与数据目录已初始化")
    elif args.command == "fixture":
        load_fixture(service.repository)
        print(f"已加载明确标注的固定测试数据，筛选日期 {FIXTURE_DATE}")
    elif args.command == "sync":
        request = SyncRequest(
            mode="dividend"
            if args.dividend
            else "namechange"
            if args.namechange
            else "incremental"
            if args.incremental
            else "initialize",
            history_refresh=args.history_refresh,
            start_date=date.fromisoformat(args.start) if args.start else None,
            end_date=date.fromisoformat(args.end) if args.end else None,
        )
        print(json.dumps(service.submit_sync(request), ensure_ascii=False))
    elif args.command == "screen":
        print(
            json.dumps(
                service.submit_screen(
                    ScreeningRequest(
                        as_of_date=args.date,
                        rule_id=args.rule,
                        parameters=json.loads(args.parameters),
                    )
                ),
                ensure_ascii=False,
            )
        )
    elif args.command == "job":
        print(json.dumps(service.job(args.id), ensure_ascii=False, indent=2))
