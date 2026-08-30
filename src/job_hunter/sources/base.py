from __future__ import annotations

import logging
from typing import Protocol

from bs4 import BeautifulSoup

from job_hunter.models import Job

logger = logging.getLogger(__name__)


class JobSource(Protocol):
    def discover(self) -> list[Job]: ...


def strip_html(text: str) -> str:
    if not text:
        return text
    soup = BeautifulSoup(text, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())
