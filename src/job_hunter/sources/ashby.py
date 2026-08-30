from __future__ import annotations

from job_hunter.models import Job

from .base import logger, strip_html

_URL_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


class AshbySource:
    def __init__(self, board: str, http) -> None:
        self._board = board
        self._http = http

    def discover(self) -> list[Job]:
        try:
            data = self._http.get_json(_URL_TEMPLATE.format(board=self._board))
        except Exception:
            logger.warning("ashby discovery failed for board %s", self._board, exc_info=True)
            return []

        jobs = []
        for item in data.get("jobs", []):
            job_id = item.get("id")
            description = item.get("descriptionPlain") or item.get("descriptionHtml", "")
            jobs.append(
                Job(
                    source="ashby",
                    source_job_id=str(job_id) if job_id is not None else None,
                    title=item.get("title", ""),
                    company=self._board,
                    location=item.get("location", ""),
                    url=item.get("jobUrl", ""),
                    description=strip_html(description),
                    remote=item.get("isRemote"),
                )
            )
        return jobs
