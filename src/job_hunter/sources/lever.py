from __future__ import annotations

from job_hunter.models import Job

from .base import logger, strip_html

_URL_TEMPLATE = "https://api.lever.co/v0/postings/{site}?mode=json"

_REMOTE_WORKPLACE_TYPES = {"remote"}
_ONSITE_WORKPLACE_TYPES = {"on-site", "onsite"}


class LeverSource:
    def __init__(self, site: str, http) -> None:
        self._site = site
        self._http = http

    def discover(self) -> list[Job]:
        try:
            data = self._http.get_json(_URL_TEMPLATE.format(site=self._site))
        except Exception:
            logger.warning("lever discovery failed for site %s", self._site, exc_info=True)
            return []

        jobs = []
        for item in data:
            categories = item.get("categories", {}) or {}
            workplace_type = (item.get("workplaceType") or "").lower()
            if workplace_type in _REMOTE_WORKPLACE_TYPES:
                remote = True
            elif workplace_type in _ONSITE_WORKPLACE_TYPES:
                remote = False
            else:
                remote = None

            description = item.get("descriptionPlain") or item.get("description", "")
            jobs.append(
                Job(
                    source="lever",
                    source_job_id=item.get("id"),
                    title=item.get("text", ""),
                    company=self._site,
                    location=categories.get("location", ""),
                    url=item.get("hostedUrl", ""),
                    description=strip_html(description),
                    remote=remote,
                )
            )
        return jobs
