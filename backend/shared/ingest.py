"""
Ingestion module — EzDocs

Handles cloning of GitHub repositories and processing of uploaded zip archives,
preparing codebases for graph analysis.
"""

import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import git
from fastapi import UploadFile

log = logging.getLogger("ezdocs.ingest")

# Default to system temp so cloned repos don't trigger uvicorn's file watcher
_DEFAULT_INGEST = os.path.join(tempfile.gettempdir(), "ezdocs_ingested")
INGEST_DIR = Path(os.getenv("EZDOCS_INGEST_DIR", _DEFAULT_INGEST))
INGEST_DIR.mkdir(parents=True, exist_ok=True)
MAX_UNZIP_BYTES = int(os.getenv("EZDOCS_MAX_UNZIP_BYTES", 500 * 1024 * 1024))
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def _fresh_dir() -> Path:
    d = INGEST_DIR / uuid.uuid4().hex
    d.mkdir(parents=True)
    return d


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _normalise_github_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("github.com/"):
        url = f"https://{url}"
    if not url.startswith("https://github.com/"):
        raise ValueError(f"Not a valid GitHub URL: {url!r}")
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _unwrap_single_dir(path: Path) -> Path:
    children = [c for c in path.iterdir() if not c.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return path


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    total_size = sum(info.file_size for info in zf.infolist())
    if total_size > MAX_UNZIP_BYTES:
        raise ValueError(
            f"Archive would expand to {total_size / 1024 / 1024:.1f} MB, "
            f"which exceeds the {MAX_UNZIP_BYTES / 1024 / 1024:.0f} MB limit."
        )
    resolved_target = target.resolve()
    for member in zf.infolist():
        member_path = (target / member.filename).resolve()
        if not str(member_path).startswith(str(resolved_target)):
            raise ValueError(f"Zip-slip detected: {member.filename!r}")
        zf.extract(member, target)


def clone_github_repo(repo_url: str) -> str:
    url = _normalise_github_url(repo_url)
    target = _fresh_dir()
    log.info("Cloning %s → %s", url, target)
    try:
        git.Repo.clone_from(url, target, depth=1)
        log.info("Clone complete: %s", target)
        return str(target)
    except git.GitCommandError as exc:
        _cleanup(target)
        raise RuntimeError(f"Git clone failed for {url!r}: {exc.stderr.strip()}") from exc
    except Exception as exc:
        _cleanup(target)
        raise RuntimeError(f"Unexpected error cloning {url!r}: {exc}") from exc


async def process_upload(file: UploadFile) -> str:
    target = _fresh_dir()
    zip_path = target / "upload.zip"
    log.info("Receiving upload '%s' → %s", file.filename, zip_path)
    try:
        with zip_path.open("wb") as fh:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                fh.write(chunk)
        if not zipfile.is_zipfile(zip_path):
            raise ValueError("Uploaded file is not a valid .zip archive.")
        extract_dir = target / "src"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            _safe_extract(zf, extract_dir)
        zip_path.unlink()
        result = _unwrap_single_dir(extract_dir)
        log.info("Upload extracted to: %s", result)
        return str(result)
    except (ValueError, zipfile.BadZipFile) as exc:
        _cleanup(target)
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _cleanup(target)
        raise RuntimeError(f"Failed to process upload: {exc}") from exc
