from __future__ import annotations

import argparse
import io
import logging
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ARTIFACTS_URL_TEMPLATE = "https://api.github.com/repos/{repo}/actions/artifacts"


def restore_state(repo: str, token: str, name: str, dest: Path, http=None) -> bool:
    http = http or requests
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    response = http.get(_ARTIFACTS_URL_TEMPLATE.format(repo=repo), headers=headers)
    response.raise_for_status()
    artifacts = response.json().get("artifacts", [])

    candidates = [a for a in artifacts if a.get("name") == name and not a.get("expired")]
    if not candidates:
        logger.info("No prior %r artifact found for %s; starting with fresh state.", name, repo)
        return False

    newest = max(candidates, key=lambda a: a["created_at"])

    download_response = http.get(newest["archive_download_url"], headers=headers)
    download_response.raise_for_status()

    dest = Path(dest)
    with zipfile.ZipFile(io.BytesIO(download_response.content)) as zf:
        member = _find_member(zf, dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            out.write(src.read())

    logger.info("Restored %s from artifact %r (id=%s)", dest, name, newest["id"])
    return True


def _find_member(zf: zipfile.ZipFile, expected_name: str) -> str:
    for info in zf.infolist():
        if info.is_dir():
            continue
        if Path(info.filename).name != expected_name:
            continue
        member_path = Path(info.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Unsafe zip member path: {info.filename!r}")
        return info.filename
    raise ValueError(f"No member named {expected_name!r} found in artifact zip")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restore_state")
    parser.add_argument("--repo", required=True, help="GitHub repo in owner/name form")
    parser.add_argument("--token", required=True, help="GitHub token with actions:read access")
    parser.add_argument("--name", default="job-hunter-state", help="Artifact name to restore")
    parser.add_argument("--dest", default="var/job_hunter.sqlite3", help="Destination path for the SQLite file")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    restore_state(repo=args.repo, token=args.token, name=args.name, dest=Path(args.dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
