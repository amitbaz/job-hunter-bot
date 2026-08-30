from __future__ import annotations
from .models import Job, SearchPolicy, PrefilterResult
from .normalize import normalize_text


def prefilter_job(job: Job, policy: SearchPolicy) -> PrefilterResult:
    title_lower = normalize_text(job.title)
    desc_lower = normalize_text(job.description)
    location_lower = normalize_text(job.location)

    # Hard block: explicitly not remote
    if job.remote is False:
        return PrefilterResult(should_evaluate=False, hard_blocker=True, reason="not remote")
    if "onsite" in location_lower or "on-site" in location_lower or "in-office" in location_lower:
        if job.remote is not True:
            return PrefilterResult(should_evaluate=False, hard_blocker=True, reason="location indicates not remote")

    # Hard block: blocked title keyword
    for kw in policy.blocked_title_keywords:
        if kw.lower() in title_lower:
            return PrefilterResult(should_evaluate=False, hard_blocker=True, reason=f"blocked title keyword: {kw}")

    # Check for target title or keyword evidence
    has_target_title = any(t.lower() in title_lower for t in policy.target_titles)
    has_keyword = any(kw.lower() in title_lower or kw.lower() in desc_lower for kw in policy.positive_keywords)

    if not has_target_title and not has_keyword:
        return PrefilterResult(should_evaluate=False, hard_blocker=False, reason="no target title or keyword match")

    return PrefilterResult(should_evaluate=True, hard_blocker=False, reason="passed prefilter")
