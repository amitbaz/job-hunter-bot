from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_hunter.cover_letter import generate_cover_letter
from job_hunter.evaluation import evaluate_job
from job_hunter.gemini import GeminiClient
from job_hunter.http import HttpClient
from job_hunter.models import DigestItem, Material, RunSummary, Settings
from job_hunter.preferences import extract_candidate_preferences, preferences_source
from job_hunter.pdf import render_cover_letter_pdf
from job_hunter.discovery import collect_candidates
from job_hunter.ranking import rank_jobs
from job_hunter.sources import build_sources
from job_hunter.store import JobStore
from job_hunter.telegram import TelegramClient, build_digest
from job_hunter.telegram import select_deliverable_items

logger = logging.getLogger(__name__)

_READY_DECISIONS = {"high_priority", "package_match"}
_MIN_DELIVERABLE_SCORE = 61


def _select_candidates(ranked, policy, preferences):
    del preferences
    return ranked[: policy.max_jobs_per_run]


def cover_letter_output_dir(settings: Settings) -> Path:
    return Path(settings.db_path).parent / "cover_letters"


def should_run_scheduled(now: datetime, timezone: str, scheduled_hour: int) -> bool:
    local_hour = now.astimezone(ZoneInfo(timezone)).hour
    return local_hour == scheduled_hour


def _requeue_pending_delivery(
    job_id: int,
    store: JobStore,
    out_dir: Path,
    digest_items: list[DigestItem],
    pdf_deliveries: list[tuple[int, Path, DigestItem]],
    summary: RunSummary,
) -> None:
    """
    Re-queue an already-evaluated job for delivery if a prior Telegram send
    failed. Never calls Gemini; reuses the persisted evaluation/cover letter.
    """
    evaluation = store.get_evaluation(job_id)
    if evaluation is None:
        return

    if evaluation.total_score < _MIN_DELIVERABLE_SCORE:
        return

    job = store.get_job(job_id)
    if job is None:
        return

    item = DigestItem(
        job_id=job_id,
        company=job.company,
        title=job.title,
        score=evaluation.total_score,
        decision=evaluation.decision,
        url=job.url,
        hard_blockers=evaluation.hard_blockers,
    )

    if not store.has_delivery(job_id, "telegram_message"):
        digest_items.append(item)

    if evaluation.decision in _READY_DECISIONS and not store.has_delivery(job_id, "telegram_document"):
        material = store.get_material(job_id)
        if material is not None:
            try:
                pdf_path = render_cover_letter_pdf(material.cover_letter_text, job.company, job.title, out_dir)
                pdf_deliveries.append((job_id, pdf_path, item))
            except Exception:
                logger.exception("cover letter PDF re-render failed for job_id=%s", job_id)
                summary.errors += 1


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
    out_dir = cover_letter_output_dir(settings)
    discovery = collect_candidates(sources, store, http, settings.policy)
    preferences = extract_candidate_preferences(settings.candidate_profile, gemini, settings.policy)
    logger.info("profile extraction: source=%s", preferences_source(preferences))
    summary.skipped += discovery.stats.prefilter_rejected + discovery.stats.profession_rejected
    ranked = rank_jobs(discovery.eligible, settings.policy)
    selected = _select_candidates(ranked, settings.policy, preferences)
    deferred_by_budget = max(0, len(ranked) - len(selected))
    logger.info("discovery: raw=%s unique=%s prefilter_rejected=%s profession_rejected=%s eligible=%s selected=%s deferred_by_budget=%s", discovery.stats.raw, discovery.stats.unique, discovery.stats.prefilter_rejected, discovery.stats.profession_rejected, discovery.stats.eligible, len(selected), deferred_by_budget)
    source_counts = {}
    for _id, job, _score in selected:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1
    logger.info("selected sources: %s", " ".join(f"{k}={source_counts[k]}" for k in sorted(source_counts)))
    queued_job_ids = {job_id for job_id, _job, _score in selected}
    for job_id in discovery.rediscovered_job_ids:
        _requeue_pending_delivery(job_id, store, out_dir, digest_items, pdf_deliveries, summary)

    for job_id, job, _score in selected:

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

    for job_id in set(store.pending_delivery_job_ids()) - queued_job_ids - set(discovery.rediscovered_job_ids):
        _requeue_pending_delivery(job_id, store, out_dir, digest_items, pdf_deliveries, summary)

    if not settings.dry_run:
        deliverable_items = select_deliverable_items(digest_items)
        if deliverable_items:
            digest_text = build_digest(deliverable_items)
            message_id = telegram.send_message(digest_text)
            if message_id is not None:
                for item in deliverable_items:
                    store.mark_delivered(item.job_id, "telegram_message", message_id)

        pdf_deliveries.sort(key=lambda entry: (-entry[2].score, (entry[2].company or "").lower(), (entry[2].title or "").lower(), entry[0]))
        for job_id, pdf_path, item in pdf_deliveries:
            caption = f"{item.company} - {item.title} - {item.score} - {item.url}"
            document_id = telegram.send_document(pdf_path, caption)
            if document_id is not None:
                store.mark_delivered(job_id, "telegram_document", document_id)

    return summary
