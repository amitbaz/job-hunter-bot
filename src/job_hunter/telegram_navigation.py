from __future__ import annotations

from job_hunter.models import DigestItem, NavigationCard

_CALLBACK_ACTIONS = {"n", "a", "x"}


def encode_callback(action: str, session_id: str, index: int) -> str:
    if action not in _CALLBACK_ACTIONS or index < 0 or not session_id:
        raise ValueError("invalid callback payload")
    payload = f"{action}|{session_id}|{index}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("callback payload exceeds Telegram limit")
    return payload


def parse_callback(data: str) -> tuple[str, str, int] | None:
    parts = data.split("|")
    if len(parts) != 3 or parts[0] not in _CALLBACK_ACTIONS or not parts[1]:
        return None
    try:
        index = int(parts[2])
    except ValueError:
        return None
    if index < 0:
        return None
    return parts[0], parts[1], index


def navigation_sort_key(item: DigestItem) -> tuple[int, str, str, int]:
    return (-item.score, (item.company or "").lower(), (item.title or "").lower(), item.job_id)


def build_navigation_card(
    card: NavigationCard,
    session_id: str,
    index: int,
    total: int,
) -> tuple[str, list[list[dict[str, str]]]]:
    if total <= 0 or index < 0 or index >= total:
        raise ValueError("invalid navigation position")

    text = (
        f"{card.title}\n\n"
        f"Company: {card.company or 'Not specified'}\n"
        f"Location: {card.location or 'Not specified'}\n"
        f"Match: {card.score}%"
    )

    actions: list[dict[str, str]] = []
    if card.url:
        actions.append({"text": "View job", "url": card.url})
    actions.append({"text": "Apply", "callback_data": encode_callback("a", session_id, index)})

    previous_index = index - 1 if index > 0 else index
    next_index = index + 1 if index < total - 1 else index
    previous_action = "n" if index > 0 else "x"
    next_action = "n" if index < total - 1 else "x"

    navigation = [
        {
            "text": "◀ Previous",
            "callback_data": encode_callback(previous_action, session_id, previous_index),
        },
        {
            "text": f"{index + 1} / {total}",
            "callback_data": encode_callback("x", session_id, index),
        },
        {
            "text": "Next ▶",
            "callback_data": encode_callback(next_action, session_id, next_index),
        },
    ]
    return text, [actions, navigation]
