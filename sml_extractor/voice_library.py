"""Download and safely install the voice library."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path


DEFAULT_REPO_ID = "ebook2audiobook/E2A-Voices"
DEFAULT_FILENAME = "voices.zip"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def has_voices(voices_dir: Path) -> bool:
    """Return whether a voice directory contains at least one WAV file."""
    return voices_dir.is_dir() and any(voices_dir.rglob("*.wav"))


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP archive while rejecting paths outside the destination."""
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Unsafe symlink in voice archive: {member.filename}")
            member_path = (destination / member.filename).resolve()
            if member_path != destination and destination not in member_path.parents:
                raise ValueError(f"Unsafe path in voice archive: {member.filename}")
        bundle.extractall(destination)


def ensure_voice_library(
    library_root: str | Path,
    repo_id: str = DEFAULT_REPO_ID,
    filename: str = DEFAULT_FILENAME,
) -> bool:
    """Install the Hub voice archive if ``library_root/voices`` is empty.

    Returns ``True`` when a download was performed and ``False`` when an
    existing voice library was reused.
    """
    library_root = Path(library_root).expanduser().resolve()
    voices_dir = library_root / "voices"
    if has_voices(voices_dir):
        return False

    from huggingface_hub import hf_hub_download

    library_root.mkdir(parents=True, exist_ok=True)
    print(f"No voices found in {voices_dir}; downloading {repo_id}/{filename}...")
    archive = Path(
        hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset")
    )

    with tempfile.TemporaryDirectory(prefix="e2a-voices-") as temp_dir:
        extracted_root = Path(temp_dir)
        _safe_extract(archive, extracted_root)
        extracted_voices = extracted_root / "voices"
        if not has_voices(extracted_voices):
            raise RuntimeError("Downloaded voice archive contains no WAV files under voices/")
        shutil.copytree(extracted_voices, voices_dir, dirs_exist_ok=True)

    if not has_voices(voices_dir):
        raise RuntimeError(f"Voice library installation failed: {voices_dir} is empty")
    print(f"Voice library installed in {voices_dir}")
    return True


def configured_library_root()->Path:
    """Return this tool's data directory, or an explicitly selected voice root."""
    configured = os.environ.get('SML_DATA_DIR') or os.environ.get('E2A_PATH')
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATA_DIR
