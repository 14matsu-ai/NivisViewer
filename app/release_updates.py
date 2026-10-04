"""Manual, bounded lookup of the latest public GitHub release."""

import json
import re
from urllib.request import Request, urlopen

REPOSITORY_URL = "https://github.com/14matsu-ai/NivisViewer"
LATEST_RELEASE_API = "https://api.github.com/repos/14matsu-ai/NivisViewer/releases/latest"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def numeric_version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        raise ValueError("invalid release version")
    return tuple(int(part) for part in match.groups())


def fetch_latest_release() -> tuple[str, str]:
    request = Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "NivisViewer"},
    )
    with urlopen(request, timeout=5) as response:
        payload = response.read(256_001)
    if len(payload) > 256_000:
        raise ValueError("release response too large")
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("no public stable release")
    tag = data.get("tag_name")
    url = data.get("html_url")
    if not isinstance(tag, str) or not isinstance(url, str):
        raise ValueError("invalid release response")
    numeric_version(tag)
    if not url.startswith(REPOSITORY_URL + "/releases/tag/"):
        raise ValueError("invalid release URL")
    return tag, url
