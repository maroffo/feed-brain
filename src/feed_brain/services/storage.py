# ABOUTME: Minimal object storage used for the public site, the vault outbox and the OPML copy.
# ABOUTME: LocalStorage (dry runs, tests, local outbox) and GcsStorage (Cloud Run, gcloud ADC on the Mac).

from pathlib import Path
from typing import Any, Protocol


class Storage(Protocol):
    def write_text(self, path: str, text: str, content_type: str) -> None: ...

    def read_text(self, path: str) -> str | None: ...

    def list(self, prefix: str) -> list[str]: ...

    def move(self, src: str, dst: str) -> None: ...


class LocalStorage:
    """Directory-backed storage; paths are relative to root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write_text(self, path: str, text: str, content_type: str) -> None:  # noqa: ARG002
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_text(self, path: str) -> str | None:
        target = self.root / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def move(self, src: str, dst: str) -> None:
        target = self.root / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.root / src).replace(target)


class GcsStorage:
    """Google Cloud Storage bucket (credentials from the environment / ADC)."""

    def __init__(self, bucket: Any) -> None:
        self.bucket = bucket

    @classmethod
    def from_name(cls, name: str) -> "GcsStorage":
        from google.cloud import storage

        return cls(storage.Client().bucket(name))

    def write_text(self, path: str, text: str, content_type: str) -> None:
        self.bucket.blob(path).upload_from_string(text, content_type=content_type)

    def read_text(self, path: str) -> str | None:
        blob = self.bucket.blob(path)
        return blob.download_as_text() if blob.exists() else None

    def list(self, prefix: str) -> list[str]:
        return sorted(
            b.name for b in self.bucket.list_blobs(prefix=prefix) if not b.name.endswith("/")
        )

    def move(self, src: str, dst: str) -> None:
        blob = self.bucket.blob(src)
        self.bucket.copy_blob(blob, self.bucket, dst)
        blob.delete()
