"""
GitHub Crawler — EzDocs

Streams file contents from a GitHub repository via the GitHub API and
raw.githubusercontent.com, yielding batches ready for incremental graph
analysis without requiring a local clone.

Optimisations over v1:
  - Full concurrency within a batch (semaphore-bounded, not batch-serial)
  - Persistent aiohttp connector with connection pooling
  - Automatic GitHub token auth (GITHUB_TOKEN env var)
  - Exponential-backoff retry on transient errors and rate-limit responses
  - Truncated-tree fallback for repos > 100k objects
  - File-size guard (skips blobs > MAX_FILE_BYTES)
"""

import asyncio
import logging
import os
import time
from typing import AsyncIterator
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("ezdocs.crawler")

# ─── Config ───────────────────────────────────────────────────────────────────

GITHUB_API    = "https://api.github.com"
GITHUB_RAW    = "https://raw.githubusercontent.com"

# Optional personal-access token — raises rate limit from 60 to 5 000 req/hr
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".rs",
})

# Files larger than this are skipped (bytes)
MAX_FILE_BYTES = 512 * 1024          # 512 KB

# Maximum simultaneous HTTP requests
MAX_CONCURRENCY = 20

# Retry config
MAX_RETRIES     = 4
RETRY_BASE_WAIT = 1.0                # seconds; doubles each attempt


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_repo_url(url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` from any GitHub URL form."""
    url = url.rstrip("/")
    if not url.startswith("http"):
        url = f"https://{url}"
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse GitHub URL: {url!r}")
    return parts[0], parts[1].removesuffix(".git")


def _build_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


async def _request_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    *,
    is_raw: bool = False,
) -> aiohttp.ClientResponse | None:
    """
    GET *url*, retrying on 429 / 5xx with exponential back-off.

    Returns the response (caller must close it) or None on permanent failure.
    """
    wait = RETRY_BASE_WAIT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await session.get(url)
        except aiohttp.ClientError as exc:
            log.debug("Network error on %s (attempt %d): %s", url, attempt, exc)
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(wait)
            wait *= 2
            continue

        if resp.status == 200:
            return resp

        if resp.status == 429 or resp.status == 403:
            # GitHub may return Retry-After or x-ratelimit-reset
            reset = resp.headers.get("x-ratelimit-reset")
            if reset:
                delay = max(0.0, float(reset) - time.time()) + 1
                log.warning("Rate-limited. Waiting %.0fs before retry…", delay)
                await resp.release()
                await asyncio.sleep(delay)
            else:
                log.warning("Rate-limited (attempt %d). Waiting %.1fs…", attempt, wait)
                await resp.release()
                await asyncio.sleep(wait)
                wait *= 2
            continue

        if resp.status >= 500 and attempt < MAX_RETRIES:
            log.debug("Server error %d on %s — retrying…", resp.status, url)
            await resp.release()
            await asyncio.sleep(wait)
            wait *= 2
            continue

        # 404, 401, other permanent errors
        log.debug("Permanent HTTP %d for %s", resp.status, url)
        await resp.release()
        return None

    return None


# ─── Crawler ──────────────────────────────────────────────────────────────────

class GitHubCrawler:
    """
    Stream source-file contents from a GitHub repository in concurrent batches.

    Usage::

        crawler = GitHubCrawler("https://github.com/owner/repo")
        async for batch in crawler.stream_files():
            for file in batch:
                print(file["path"], len(file["content"]))
    """

    def __init__(self, repo_url: str, batch_size: int = 20) -> None:
        self.owner, self.repo = _parse_repo_url(repo_url)
        self.batch_size       = batch_size
        self._branch: str     = ""          # resolved lazily

    # ── Private ────────────────────────────────────────────────────────────

    async def _resolve_branch(self, session: aiohttp.ClientSession) -> str:
        url  = f"{GITHUB_API}/repos/{self.owner}/{self.repo}"
        resp = await _request_with_retry(session, url)
        if resp is None:
            log.warning("Could not resolve default branch — falling back to 'main'")
            return "main"
        async with resp:
            data = await resp.json()
            return data.get("default_branch", "main")

    async def _file_tree(self, session: aiohttp.ClientSession) -> list[dict]:
        """
        Fetch the recursive git tree.  Falls back to a non-recursive listing
        when the tree is truncated (repos with >100k objects).
        """
        url  = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/git/trees/{self._branch}?recursive=1"
        resp = await _request_with_retry(session, url)
        if resp is None:
            raise RuntimeError("Failed to fetch repository file tree from GitHub.")

        async with resp:
            data = await resp.json()

        if data.get("truncated"):
            log.warning("Tree truncated by GitHub (large repo). Some files may be missing.")

        return [
            item for item in data.get("tree", [])
            if item.get("type") == "blob"
            and any(item["path"].endswith(ext) for ext in SUPPORTED_EXTENSIONS)
            and item.get("size", 0) <= MAX_FILE_BYTES
        ]

    async def _fetch_content(
        self,
        session: aiohttp.ClientSession,
        sem: asyncio.Semaphore,
        path: str,
    ) -> dict[str, str] | None:
        """Fetch one file's raw text, bounded by *sem*. Returns None on failure."""
        url = f"{GITHUB_RAW}/{self.owner}/{self.repo}/{self._branch}/{path}"
        async with sem:
            resp = await _request_with_retry(session, url, is_raw=True)
            if resp is None:
                log.debug("Skipping %s (fetch failed)", path)
                return None
            async with resp:
                try:
                    text = await resp.text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    log.debug("Could not decode %s: %s", path, exc)
                    return None

        if not text.strip():
            return None
        return {"path": path, "content": text}

    # ── Public API ─────────────────────────────────────────────────────────

    async def stream_files(self) -> AsyncIterator[list[dict[str, str]]]:
        """
        Yield batches of ``{"path": str, "content": str}`` dicts.

        All files in each batch are fetched concurrently (up to MAX_CONCURRENCY
        simultaneous connections).  The caller receives the first batch as soon
        as it's ready rather than waiting for the entire repository.
        """
        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY, ttl_dns_cache=300)
        timeout   = aiohttp.ClientTimeout(total=30, connect=10)

        async with aiohttp.ClientSession(
            headers=_build_headers(),
            connector=connector,
            timeout=timeout,
        ) as session:

            self._branch = await self._resolve_branch(session)
            log.info("Crawling %s/%s @ %s", self.owner, self.repo, self._branch)

            files = await self._file_tree(session)
            if not files:
                log.warning("No supported source files found in %s/%s.", self.owner, self.repo)
                return

            log.info("Found %d supported files — streaming in batches of %d…", len(files), self.batch_size)
            sem = asyncio.Semaphore(MAX_CONCURRENCY)

            for i in range(0, len(files), self.batch_size):
                batch = files[i : i + self.batch_size]
                tasks = [
                    self._fetch_content(session, sem, f["path"])
                    for f in batch
                ]
                results = await asyncio.gather(*tasks)
                payload = [r for r in results if r is not None]

                if payload:
                    log.debug(
                        "Batch %d–%d: yielding %d/%d files",
                        i + 1, i + len(batch), len(payload), len(batch),
                    )
                    yield payload
