from __future__ import annotations

from job_hunter.models import Job

from .base import logger, strip_html

_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseSource:
    def __init__(self, token: str, http) -> None:
        self._token = token
        self._http = http

    def discover(self) -> list[Job]:
        try:
            data = self._http.get_json(_URL_TEMPLATE.format(token=self._token))
        except Exception:
            logger.warning("greenhouse discovery failed for token %s", self._token, exc_info=True)
            return []

        jobs = []
        for item in data.get("jobs", []):
            job_id = item.get("id")
            location = item.get("location", {}) or {}
            location_name = location.get("name", "") if isinstance(location, dict) else str(location)
            jobs.append(
                Job(
                    source="greenhouse",
                    source_job_id=str(job_id) if job_id is not None else None,
                    title=item.get("title", ""),
                    company=self._token,
                    location=location_name,
                    url=item.get("absolute_url", ""),
                    description=strip_html(item.get("content", "")),
                    remote="remote" in location_name.lower() if location_name else None,
                )
            )
        return jobs
