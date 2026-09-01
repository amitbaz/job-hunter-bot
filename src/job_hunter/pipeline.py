from __future__ import annotations

import logging
import secrets
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from job_hunter.cover_letter import generate_cover_letter
from job_hunter.discovery import collect_candidates
from job_hunter.evaluation import evaluate_job
from job_hunter.gemini import GeminiClient
from job_hunter.http import HttpClient
from job_hunter.models import (
    DigestItem,
    Material,
    NavigationCard,
    NavigationSession,
    RunSummary,
    Settings,
)
from job_hunter.pdf import render_cover_letter_pdf
from job_hunter.preferences import extract_candidate_preferences, preferences_source
from job_hunter.ranking import rank_jobs, select_diverse_candidates
from job_hunter.sources import build_sources
from job_hunter.store import JobStore
from job_hunter.telegram import TelegramClient, select_deliverable_items
from job_hunter.telegram_navigation import build_navigation_card, navigation_sort_key

logger = logging.getLogger(__name__)

_READY_DECISIONS = {"high_priority", "package_match"}
_MIN_DELIVERABLE_SCORE = 61
_NAVIGATION_SESSION_TTL = timedelta(days=30)


def _select_candidates(ranked, policy, preferences):
    if not ranked or policy.max_jobs_per_run <= 0:
        return []

    try:
        if preferences is None:
            return ranked[: policy.max_jobs_per_run]
        return select_diverse_candidates(
            ranked,
            limit=policy.max_jobs_per_run,
            minimum_per_source=policy.source_minimum_per_run,
            max_share=policy.source_max_share,
        )
    except Exception:
        logger.exception("shortlist selection failed; falling back to stable global ranking")
        return ranked[: policy.max_jobs_per_run]


def _source_counts(items) -> dict[str, int]:
    return dict(sorted(Counter(job.source for _job_id, job, _score in items).items()))


def _format_source_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return " ".join(f"{source}={count}" for source, count in counts.items())


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
    """Re-queue persisted delivery work without calling Gemini again."""
    evaluation = store.get_evaluation(job_id)
    if evaluation is None or evaluation.total_score < _MIN_DELIVERABLE_SCORE:
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
        location=job.location,
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


def _build_navigation_session(items: list[DigestItem], now: datetime) -> NavigationSession:
    ordered = sorted(items, key=navigation_sort_key)
    return NavigationSession(
        session_id=secrets.token_urlsafe(12),
        cards=[
            NavigationCard(
                job_id=item.job_id,
                title=item.title,
                company=item.company,
                location=item.location,
                score=item.score,
                url=item.url,
            )
            for item in ordered
        ],
        telegram_message_id=None,
        created_at=now.isoformat(),
        expires_at=(now + _NAVIGATION_SESSION_TTL).isoformat(),
    )


def _send_navigation_card(telegram, text: str, keyboard: list[list[dict[str, str]]]) -> str | None:
    """Use the navigator API, with a legacy fallback for injected test clients."""
    send_job_card = getattr(telegram, "send_job_card", None)
    if callable(send_job_card):
        return send_job_card(text, keyboard)
    return telegram.send_message(text)


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
    profile_mode = preferences_source(preferences)
    logger.info("profile extraction: source=%s", profile_mode)
    summary.skipped += discovery.stats.prefilter_rejected + discovery.stats.profession_rejected
    ranked = rank_jobs(discovery.eligible, settings.policy, preferences)
    selected = _select_candidates(ranked, settings.policy, preferences)
    eligible_source_counts = _source_counts(ranked)
    selected_source_counts = _source_counts(selected)
    deferred_by_budget = max(0, len(ranked) - len(selected))
    logger.info(
        "discovery: raw=%s unique=%s prefilter_rejected=%s profession_rejected=%s eligible=%s selected=%s deferred_by_budget=%s sources=%s",
        discovery.stats.raw,
        discovery.stats.unique,
        discovery.stats.prefilter_rejected,
        discovery.stats.profession_rejected,
        discovery.stats.eligible,
        len(selected),
        deferred_by_budget,
        len(eligible_source_counts),
    )
    logger.info("eligible sources: %s", _format_source_counts(eligible_source_counts))
    logger.info("selected sources: %s", _format_source_counts(selected_source_counts))
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
            location=job.location,
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

        # Preserve the existing PDF behavior and send documents before the navigator
        # so the job card remains the most recent Telegram message.
        pdf_deliveries.sort(
            key=lambda entry: (
                -entry[2].score,
                (entry[2].company or "").lower(),
                (entry[2].title or "").lower(),
                entry[0],
            )
        )
        for job_id, pdf_path, item in pdf_deliveries:
            caption = f"{item.company} - {item.title} - {item.score} - {item.url}"
            document_id = telegram.send_document(pdf_path, caption)
            if document_id is not None:
                store.mark_delivered(job_id, "telegram_document", document_id)

        if deliverable_items:
            now = datetime.now(timezone.utc)
            store.prune_navigation_sessions(now.isoformat())
            session = _build_navigation_session(deliverable_items, now)
            store.create_navigation_session(session)
            text, keyboard = build_navigation_card(
                session.cards[0],
                session.session_id,
                0,
                len(session.cards),
            )
            message_id = _send_navigation_card(telegram, text, keyboard)
            if message_id is not None:
                store.attach_navigation_message_id(session.session_id, str(message_id))
                for card in session.cards:
                    store.mark_delivered(card.job_id, "telegram_message", str(message_id))

    return summary
