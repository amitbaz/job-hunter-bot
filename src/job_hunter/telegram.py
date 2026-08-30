from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from job_hunter.models import DigestItem

if TYPE_CHECKING:
    from job_hunter.http import HttpClient

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}"
_CAPTION_LIMIT = 1000

_GROUP_HEADERS = {
    "high_priority": "Ready to apply",
    "package_match": "Ready to apply",
    "possible_match": "Possible matches",
    "blocked": "Needs review / blockers",
}
_GROUP_ORDER = ("Ready to apply", "Possible matches", "Needs review / blockers")


def _digest_line(item: DigestItem) -> str:
    line = f"- {item.score} | {item.company} - {item.title}"
    if item.url:
        line += f" | {item.url}"
    if item.hard_blockers:
        line += f" | blockers: {', '.join(item.hard_blockers)}"
    return line


def build_digest(items: Sequence[DigestItem]) -> str:
    grouped: dict[str, list[DigestItem]] = {header: [] for header in _GROUP_ORDER}
    for item in items:
        header = _GROUP_HEADERS.get(item.decision, "Needs review / blockers")
        grouped[header].append(item)

    sections = []
    for header in _GROUP_ORDER:
        group_items = grouped[header]
        if not group_items:
            continue
        sections.append("\n".join([header, *(_digest_line(item) for item in group_items)]))

    return "\n\n".join(sections) if sections else "No matching jobs today."


def chunk_message(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.append(line[:limit])
            line = line[limit:]

        line_len = len(line) + (1 if current else 0)
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
            line_len = len(line)

        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def _truncate_caption(caption: str, limit: int = _CAPTION_LIMIT) -> str:
    if len(caption) <= limit:
        return caption
    return caption[: limit - 1].rstrip() + "…"


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http: "HttpClient") -> None:
        self._chat_id = chat_id
        self._http = http
        self._base_url = _BASE_URL.format(token=bot_token)

    def send_message(self, text: str) -> str | None:
        last_message_id = None
        for chunk in chunk_message(text):
            response = self._http.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self._chat_id, "text": chunk},
            )
            if response.status_code >= 400:
                logger.warning("telegram sendMessage failed: %s", response.text)
                return None
            last_message_id = response.json().get("result", {}).get("message_id")

        return str(last_message_id) if last_message_id is not None else None

    def send_document(self, path: Path, caption: str) -> str | None:
        path = Path(path)
        caption = _truncate_caption(caption)

        with open(path, "rb") as fh:
            response = self._http.post(
                f"{self._base_url}/sendDocument",
                data={"chat_id": self._chat_id, "caption": caption},
                files={"document": (path.name, fh)},
            )

        if response.status_code >= 400:
            logger.warning("telegram sendDocument failed: %s", response.text)
            return None

        message_id = response.json().get("result", {}).get("message_id")
        return str(message_id) if message_id is not None else None
