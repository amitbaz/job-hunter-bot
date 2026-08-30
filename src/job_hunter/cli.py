from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from job_hunter.config import load_settings
from job_hunter.pipeline import cover_letter_output_dir, run_pipeline, should_run_scheduled

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_hunter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Discover, evaluate, and deliver jobs")
    run_parser.add_argument("--scheduled", action="store_true", help="Only proceed at the configured scheduled hour")
    run_parser.add_argument("--config", default="config/search.yml", help="Path to search.yml")

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except Exception:
        logger.exception("job hunter run failed")
        return 1


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.config))

    if args.scheduled:
        now = datetime.now(timezone.utc)
        if not should_run_scheduled(now, settings.timezone, settings.scheduled_hour):
            logger.info(
                "Scheduled run skipped: current time is not the configured %s:00 %s slot "
                "(this is the DST duplicate cron trigger).",
                settings.scheduled_hour,
                settings.timezone,
            )
            return 0

    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    cover_letter_output_dir(settings).mkdir(parents=True, exist_ok=True)

    summary = run_pipeline(settings)
    logger.info(
        "Run complete: ready_to_apply=%d possible_matches=%d skipped=%d errors=%d",
        summary.ready_to_apply,
        summary.possible_matches,
        summary.skipped,
        summary.errors,
    )
    return 0
