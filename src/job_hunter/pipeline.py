from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from job_hunter.canonical import CanonicalResolver, parse_supported_ats_url
from job_hunter.cover_letter import generate_cover_letter
from job_hunter.discovery import collect_candidates, metric_source_label
from job_hunter.evaluation import evaluate_job
from job_hunter.gemini import GeminiClient
from job_hunter.http import HttpClient
from job_hunter.job_identity import normalize_company_name
from job_hunter.models import (
    AtsReference,
    DigestItem,
    Job,
    Material,
    ReviewItem,
    RunSummary,
    Settings,
)
from job_hunter.preferences import extract_candidate_preferences, preferences_source
from job_hunter.pdf import render_cover_letter_pdf
from job_hunter.ranking import rank_jobs, select_diverse_candidates
from job_hunter.sources import (
    CompanyWatchSource,
    DuckDuckGoSource,
    GmailStagedSource,
    build_sources,
)
from job_hunter.store import JobStore
from job_hunter.telegram import (
    TelegramClient,
    build_digest,
    build_gmail_review_digest_chunks,
)
from job_hunter.telegram import select_deliverable_items
from job_hunter.watchlist import promote_company, sync_manual_watch_seeds

logger = logging.getLogger(__name__)

_READY_DECISIONS = {"high_priority", "package_match"}
_MIN_DELIVERABLE_SCORE = 61
_SUPPORTED_WATCH_ATS_PROVIDERS = frozenset({"ashby", "greenhouse", "lever"})
_CANONICAL_SEARCH_SITES = (
    "site:jobs.ashbyhq.com OR site:jobs.lever.co OR "
    "site:boards.greenhouse.io OR careers"
)


def _targeted_canonical_candidates(http: HttpClient, job: Job) -> list[Job]:
    """Run one bounded public search for the employer's original posting."""
    company = " ".join(job.company.replace('"', " ").split())
    title = " ".join(job.title.replace('"', " ").split())
    if not company or not title:
        return []

    query = f'"{company}" "{title}" ({_CANONICAL_SEARCH_SITES})'
    candidates = DuckDuckGoSource(http, [query]).discover()
    for candidate in candidates:
        ats = parse_supported_ats_url(candidate.url)
        if ats is not None and (
            normalize_company_name(ats.board) == normalize_company_name(job.company)
        ):
            candidate.company = ats.board
    return candidates


def _persisted_watch_target(
    store: JobStore, company_name: str
) -> AtsReference | None:
    """Return only a persisted, complete, supported ATS watch target."""
    watch = store.get_company_watch(company_name)
    if watch is None:
        return None

    provider = (watch["ats_provider"] or "").strip().lower()
    identifier = (watch["ats_identifier"] or "").strip()
    if provider not in _SUPPORTED_WATCH_ATS_PROVIDERS or not identifier:
        return None
    return AtsReference(provider=provider, board=identifier, job_id=None)


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
    return dict(
        sorted(
            Counter(
                metric_source_label(job.source)
                for _job_id, job, _score in items
            ).items()
        )
    )


def _format_source_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return " ".join(f"{source}={count}" for source, count in counts.items())


def _due_watch_state(
    store: JobStore,
) -> dict[int, tuple[str, str | None, int, str | None]]:
    """Snapshot due watch health so logs count persisted check outcomes only."""
    return {
        watch["id"]: (
            watch["company_name"],
            watch["last_verified_at"],
            watch["consecutive_failures"],
            watch["paused_until"],
        )
        for watch in store.list_due_company_watches(datetime.now(timezone.utc))
    }


def _watch_check_outcomes(
    store: JobStore,
    before: dict[int, tuple[str, str | None, int, str | None]],
) -> tuple[int, int]:
    """Return persisted successful/failed checks and newly applied pauses."""
    checks = 0
    paused = 0
    for _watch_id, (
        company_name,
        verified_at,
        failures,
        paused_until,
    ) in before.items():
        watch = store.get_company_watch(company_name)
        if watch is None:
            continue
        check_recorded = (
            watch["last_verified_at"] != verified_at
            or watch["consecutive_failures"] != failures
        )
        if check_recorded:
            checks += 1
        if (
            watch["paused_until"] is not None
            and watch["paused_until"] != paused_until
        ):
            paused += 1
    return checks, paused


def _watch_promotion_state(watch) -> tuple[object, ...] | None:
    """Return the persisted fields that constitute a meaningful promotion."""
    if watch is None:
        return None
    return (
        watch["promotion_source"],
        watch["careers_url"],
        watch["ats_provider"],
        watch["ats_identifier"],
        watch["confidence"],
    )


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
    store = store or JobStore(settings.db_path)
    try:
        sync_manual_watch_seeds(store, settings.policy.manual_company_watch)
    except Exception:
        logger.exception("manual company watch sync failed")

    base_sources = sources if sources is not None else build_sources(settings, http)
    sources = [
        *base_sources,
        GmailStagedSource(store),
        CompanyWatchSource(store, http),
    ]
    due_watches = _due_watch_state(store)
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: _targeted_canonical_candidates(http, job),
        watch_target=lambda company: _persisted_watch_target(store, company),
    )
    gemini = gemini or GeminiClient(settings.gemini_api_key, settings.gemini_model, http)
    if telegram is None and not settings.dry_run:
        telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id, http)

    summary = RunSummary()
    digest_items: list[DigestItem] = []
    pdf_deliveries: list[tuple[int, Path, DigestItem]] = []
    out_dir = cover_letter_output_dir(settings)
    discovery = collect_candidates(
        sources,
        store,
        http,
        settings.policy,
        resolver=resolver,
    )
    watch_checks, watch_paused = _watch_check_outcomes(store, due_watches)
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
    companies_promoted = 0
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
        try:
            promotion_before = _watch_promotion_state(
                store.get_company_watch(job.company)
            )
            promoted_watch_id = promote_company(
                store,
                job_id=job_id,
                job=job,
                evaluation=evaluation,
                package_threshold=settings.policy.thresholds["package"],
            )
            promotion_after = _watch_promotion_state(
                store.get_company_watch(job.company)
            )
            if promoted_watch_id is not None and promotion_after != promotion_before:
                companies_promoted += 1
        except Exception:
            logger.exception("company watch promotion failed for job_id=%s", job_id)
            summary.errors += 1

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

        pending_reviews = store.pending_review_events()
        if pending_reviews:
            review_items = [
                ReviewItem(
                    event_id=row["id"],
                    company=row["company"],
                    role_title=row["role_title"],
                    occurred_at=row["occurred_at"],
                    subject=row["subject"],
                    rationale=row["rationale"],
                )
                for row in pending_reviews
            ]
            for review_text, event_ids in build_gmail_review_digest_chunks(review_items):
                review_message_id = telegram.send_message(review_text)
                if review_message_id is None:
                    break
                store.mark_review_delivered(event_ids, review_message_id)

    logger.info(
        "company watch outcomes: companies_promoted=%s watch_checks=%s watch_paused=%s",
        companies_promoted,
        watch_checks,
        watch_paused,
    )

    return summary
