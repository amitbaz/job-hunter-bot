from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_hunter.cover_letter import generate_cover_letter
from job_hunter.evaluation import evaluate_job
from job_hunter.fetching import enrich_job
from job_hunter.gemini import GeminiClient
from job_hunter.http import HttpClient
from job_hunter.models import DigestItem, Material, RunSummary, Settings
from job_hunter.pdf import render_cover_letter_pdf
from job_hunter.prefilter import prefilter_job
from job_hunter.sources import build_sources
from job_hunter.store import JobStore
from job_hunter.telegram import TelegramClient, build_digest

logger = logging.getLogger(__name__)

_READY_DECISIONS = {"high_priority", "package_match"}


def cover_letter_output_dir(settings: Settings) -> Path:
    return Path(settings.db_path).parent / "cover_letters"


def should_run_scheduled(now: datetime, timezone: str, scheduled_hour: int) -> bool:
    local_hour = now.astimezone(ZoneInfo(timezone)).hour
    return local_hour == scheduled_hour


def run_pipeline(
    settings: Settings,
    sources=None,
    store: JobStore | None = None,
    gemini: GeminiClient | None = None,
    telegram: TelegramClient | None = None,
    http: HttpClient | None = None,
) -> RunSummary:
    http = http or HttpClient()
    sources = sources if sources is not None else build_sources(settings, http)
    store = store or JobStore(settings.db_path)
    gemini = gemini or GeminiClient(settings.gemini_api_key, settings.gemini_model, http)
    if telegram is None and not settings.dry_run:
        telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, http)

    summary = RunSummary()
    digest_items: list[DigestItem] = []
    pdf_deliveries: list[tuple[int, Path, DigestItem]] = []
    evaluated_count = 0
    out_dir = cover_letter_output_dir(settings)

    for source in sources:
        try:
            jobs = source.discover()
        except Exception:
            logger.exception("source discovery failed: %r", source)
            continue

        for job in jobs:
            if job.url:
                enrich_job(job, http)

            job_id, _is_new, _description_changed = store.upsert_job(job)

            if not store.needs_evaluation(job_id):
                continue

            prefilter_result = prefilter_job(job, settings.policy)
            if not prefilter_result.should_evaluate:
                summary.skipped += 1
                continue

            if evaluated_count >= settings.policy.max_jobs_per_run:
                continue
            evaluated_count += 1

            try:
                evaluation = evaluate_job(job, settings.candidate_profile, settings.policy, gemini)
            except Exception:
                logger.exception("evaluation failed for job_id=%s", job_id)
                summary.errors += 1
                continue

            store.save_evaluation(job_id, evaluation)

            item = DigestItem(
                job_id=job_id,
                company=job.company,
                title=job.title,
                score=evaluation.total_score,
                decision=evaluation.decision,
                url=job.url,
                hard_blockers=evaluation.hard_blockers,
            )
            digest_items.append(item)

            if evaluation.decision in _READY_DECISIONS:
                summary.ready_to_apply += 1
            elif evaluation.decision == "possible_match":
                summary.possible_matches += 1
            else:
                summary.skipped += 1

            if evaluation.decision in _READY_DECISIONS:
                try:
                    text = generate_cover_letter(
                        job,
                        evaluation,
                        settings.candidate_profile,
                        settings.cover_letter_template,
                        gemini,
                        date.today(),
                    )
                    store.save_material(job_id, Material(job_id=job_id, cover_letter_text=text))
                    pdf_path = render_cover_letter_pdf(text, job.company, job.title, out_dir)
                    pdf_deliveries.append((job_id, pdf_path, item))
                except Exception:
                    logger.exception("cover letter/PDF generation failed for job_id=%s", job_id)
                    summary.errors += 1

    if not settings.dry_run and digest_items:
        digest_text = build_digest(digest_items)
        message_id = telegram.send_message(digest_text)
        if message_id is not None:
            for item in digest_items:
                store.mark_delivered(item.job_id, "telegram_message", message_id)

        for job_id, pdf_path, item in pdf_deliveries:
            caption = f"{item.company} - {item.title} - {item.score} - {item.url}"
            document_id = telegram.send_document(pdf_path, caption)
            if document_id is not None:
                store.mark_delivered(job_id, "telegram_document", document_id)

    return summary
