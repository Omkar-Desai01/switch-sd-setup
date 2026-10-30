import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://api.github.com"
USER_AGENT = "sdsetup/0.1"


class FetchError(Exception):
    pass


class RateLimitError(FetchError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    digest: str = None  # lowercase sha256 hex, or None when not published


@dataclass(frozen=True)
class Release:
    repo: str
    tag: str
    assets: tuple


@dataclass(frozen=True)
class Resolved:
    component_id: str
    tag: str
    asset_name: str
    path: Path
    sha256: str
    digest_verified: bool


def parse_release(repo, data):
    assets = []
    for a in data.get("assets", []):
        digest = a.get("digest") or ""
        digest = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
        assets.append(Asset(a["name"], a["browser_download_url"], int(a.get("size", 0)), digest))
    return Release(repo, data["tag_name"], tuple(assets))


def pick_asset(release, pattern):
    rx = re.compile(pattern)
    hits = [a for a in release.assets if rx.search(a.name)]
    if not hits:
        raise FetchError(f"{release.repo} {release.tag}: no asset matches {pattern}")
    if len(hits) > 1:
        names = ", ".join(a.name for a in hits)
        raise FetchError(f"{release.repo} {release.tag}: {pattern} is ambiguous ({names})")
    return hits[0]


def _request(url, token=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def latest_release(repo, *, token=None, opener=urllib.request.urlopen, cache_dir=None, max_age=3600):
    cache_file = None
    if cache_dir:
        cache_file = Path(cache_dir) / "api" / (repo.replace("/", "__") + ".json")
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < max_age:
            return parse_release(repo, json.loads(cache_file.read_text(encoding="utf-8")))
    try:
        with opener(_request(f"{API}/repos/{repo}/releases/latest", token), timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("message", "")
            except Exception:
                pass
            if "rate limit" in detail.lower():
                raise RateLimitError(
                    "GitHub API rate limit hit (60 requests/hour without a token). "
                    "Set the GITHUB_TOKEN environment variable or retry later."
                ) from e
            raise FetchError(f"{repo}: access blocked ({detail or e.code}); not downloadable from GitHub") from e
        if e.code == 404:
            raise FetchError(f"{repo}: no release found (repo missing, private or blocked)") from e
        raise FetchError(f"{repo}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"{repo}: network error: {e.reason}") from e
    data = json.loads(body)
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
    return parse_release(repo, data)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_asset(asset, dest_dir, *, opener=urllib.request.urlopen, progress=None):
    """Download asset into dest_dir. Returns (path, sha256, digest_verified)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / asset.name
    if path.exists():
        actual = sha256_file(path)
        if asset.digest is None or actual == asset.digest:
            return path, actual, asset.digest is not None
        path.unlink()
    part = path.with_name(path.name + ".part")
    h = hashlib.sha256()
    done = 0
    try:
        with opener(_request(asset.url), timeout=60) as resp, open(part, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(asset.name, done, asset.size)
    except urllib.error.URLError as e:
        part.unlink(missing_ok=True)
        raise FetchError(f"{asset.name}: download failed: {e}") from e
    actual = h.hexdigest()
    if asset.size and done != asset.size:
        part.unlink(missing_ok=True)
        raise FetchError(f"{asset.name}: size mismatch ({done} != {asset.size})")
    if asset.digest and actual != asset.digest:
        part.unlink(missing_ok=True)
        raise FetchError(f"{asset.name}: sha256 mismatch (got {actual}, expected {asset.digest})")
    os.replace(part, path)
    return path, actual, asset.digest is not None


def literal_asset_name(pattern):
    """Plain file name an asset pattern matches exactly (e.g. Goldleaf.nro), or None if it is a real regex."""
    body = pattern[1:] if pattern.startswith("^") else pattern
    body = body[:-1] if body.endswith("$") else body
    name = body.replace("\\.", ".")
    return name if re.fullmatch(r"[A-Za-z0-9_.\-]+", name) else None


def latest_tag_without_api(repo, opener=urllib.request.urlopen):
    """Tag of the latest release read from the github.com redirect (not subject to the API rate limit)."""
    try:
        with opener(_request(f"https://github.com/{repo}/releases/latest"), timeout=30) as resp:
            final = resp.geturl()
    except urllib.error.URLError as e:
        raise FetchError(f"{repo}: cannot find the latest release: {e}") from e
    m = re.search(r"/releases/tag/(.+)$", final)
    if not m:
        raise FetchError(f"{repo}: no release found")
    return urllib.parse.unquote(m.group(1))


class GithubProvider:
    """Resolves a manifest component to a verified local file."""

    def __init__(self, cache_dir, *, token=None, opener=urllib.request.urlopen, progress=None):
        self.cache_dir = Path(cache_dir)
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.opener = opener
        self.progress = progress

    def _without_api(self, component, limit_error):
        """API rate-limited: fixed-name assets can still be fetched by URL, without a published digest."""
        name = literal_asset_name(component.asset)
        if not name:
            raise limit_error
        tag = latest_tag_without_api(component.repo, self.opener)
        asset = Asset(name, f"https://github.com/{component.repo}/releases/download/{urllib.parse.quote(tag, safe='')}/{name}", 0, None)
        dest = self.cache_dir / "downloads" / component.repo.replace("/", "__") / tag.replace("/", "_")
        path, sha, verified = download_asset(asset, dest, opener=self.opener, progress=self.progress)
        return Resolved(component.id, tag, name, path, sha, verified)

    def get(self, component):
        try:
            release = latest_release(
                component.repo, token=self.token, opener=self.opener, cache_dir=self.cache_dir
            )
        except RateLimitError as e:
            return self._without_api(component, e)
        asset = pick_asset(release, component.asset)
        dest = self.cache_dir / "downloads" / component.repo.replace("/", "__") / release.tag
        path, sha, verified = download_asset(
            asset, dest, opener=self.opener, progress=self.progress
        )
        return Resolved(component.id, release.tag, asset.name, path, sha, verified)
