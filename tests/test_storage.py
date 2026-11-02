# ABOUTME: Tests for the storage backends used by publishing, the vault outbox and OPML sync.
# ABOUTME: LocalStorage is exercised directly; GcsStorage is checked against a fake bucket client.

from unittest.mock import MagicMock

from feed_brain.services.storage import GcsStorage, LocalStorage


def test_local_storage_roundtrip_list_move(tmp_path):
    store = LocalStorage(tmp_path)
    store.write_text("a/b/one.md", "uno", "text/markdown")
    store.write_text("a/b/two.json", "{}", "application/json")
    store.write_text("a/c/three.md", "tre", "text/markdown")

    assert store.read_text("a/b/one.md") == "uno"
    assert store.read_text("missing.md") is None
    assert store.list("a/b/") == ["a/b/one.md", "a/b/two.json"]

    store.move("a/b/one.md", "done/one.md")
    assert store.list("a/b/") == ["a/b/two.json"]
    assert (tmp_path / "done/one.md").read_text() == "uno"


def test_gcs_storage_uses_bucket_api():
    bucket = MagicMock()
    blob = MagicMock()
    blob.exists.return_value = True
    blob.download_as_text.return_value = "content"
    bucket.blob.return_value = blob
    listed = MagicMock()
    listed.name = "p/x.json"
    folder_marker = MagicMock()
    folder_marker.name = "p/"
    bucket.list_blobs.return_value = [listed, folder_marker]

    store = GcsStorage(bucket)
    store.write_text("site/index.html", "<html>", "text/html; charset=utf-8")
    blob.upload_from_string.assert_called_with("<html>", content_type="text/html; charset=utf-8")
    assert store.read_text("p/x.json") == "content"
    assert store.list("p/") == ["p/x.json"]

    store.move("p/x.json", "done/x.json")
    bucket.copy_blob.assert_called_once_with(blob, bucket, "done/x.json")
    blob.delete.assert_called_once()
