from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_hunter.http import HttpClient

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model: str, http: HttpClient) -> None:
        self._api_key = api_key
        self.model = model
        self._http = http

    def generate_text(self, prompt: str, *, json_mode: bool = False) -> str:
        url = f"{_BASE_URL}/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
        if json_mode:
            payload["generationConfig"] = {"responseMimeType": "application/json"}

        response = self._http.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise GeminiError(f"Gemini API error {response.status_code}: {response.text}")

        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeminiError("Gemini response missing content") from exc

        if not text:
            raise GeminiError("Gemini response missing content")

        return text
