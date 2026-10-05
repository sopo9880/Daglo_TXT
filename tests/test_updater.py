import hashlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from updater import Update, check_update, checksum_for, download_update, parse_release, version_tuple


def release(version="1.2.0"):
    name = f"DagloTXT-Setup-{version}.exe"
    base = f"https://github.com/sopo9880/Daglo_TXT/releases/download/v{version}/"
    return {"tag_name": f"v{version}", "body": "변경 사항", "assets": [
        {"name": name, "size": 8, "browser_download_url": base + name},
        {"name": "SHA256SUMS.txt", "browser_download_url": base + "SHA256SUMS.txt"}]}


class UpdaterTests(unittest.TestCase):
    def test_numeric_version_comparison(self):
        self.assertGreater(version_tuple("v1.10.0"), version_tuple("1.9.0"))
        self.assertIsNone(parse_release(release("1.1.0")))
        self.assertIsNone(parse_release(release("1.0.0")))
        self.assertEqual(parse_release(release()).version, "1.2.0")

    def test_invalid_version(self):
        for value in ("1.2.0-beta", "../../run", "1.2", ""):
            with self.assertRaises(ValueError):
                version_tuple(value)

    def test_unpublished_releases(self):
        for flag in ("draft", "prerelease"):
            value = release()
            value[flag] = True
            self.assertIsNone(parse_release(value))

    def test_missing_asset_and_checksum(self):
        for index in (0, 1):
            value = release()
            value["assets"].pop(index)
            with self.assertRaises(ValueError):
                parse_release(value)

    def test_foreign_download_rejected(self):
        for url in ("https://example.com/file.exe", "https://github.com/other/repo/releases/download/v1.2.0/file.exe"):
            value = release()
            value["assets"][0]["browser_download_url"] = url
            with self.assertRaises(ValueError):
                parse_release(value)

    def test_invalid_size(self):
        for size in (0, -1, 1024 ** 3, "8"):
            value = release()
            value["assets"][0]["size"] = size
            with self.assertRaises(ValueError):
                parse_release(value)

    def test_checksum_exact_name_and_duplicates(self):
        digest = "a" * 64
        self.assertEqual(checksum_for(f"{digest}  expected.exe", "expected.exe"), digest)
        for text in (f"{digest}  other.exe", f"{digest}  expected.exe\n{digest}  expected.exe"):
            with self.assertRaises(ValueError):
                checksum_for(text, "expected.exe")

    def test_download_verified(self):
        data = b"MZsample"
        digest = hashlib.sha256(data).hexdigest()
        update = parse_release(release())
        with tempfile.TemporaryDirectory() as directory, patch("updater.read_small", return_value=f"{digest}  {update.name}".encode()), patch("updater.urllib.request.urlopen", return_value=io.BytesIO(data)):
            progress = []
            path = download_update(update, directory, lambda a, b: progress.append((a, b)))
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(progress[-1], (8, 8))

    def test_corrupted_truncated_and_oversized_downloads(self):
        update = parse_release(release())
        for data in (b"corrupt!", b"MZ", b"MZsampleEXTRA"):
            with tempfile.TemporaryDirectory() as directory, patch("updater.read_small", return_value=f"{'a' * 64}  {update.name}".encode()), patch("updater.urllib.request.urlopen", return_value=io.BytesIO(data)):
                with self.assertRaises(ValueError):
                    download_update(update, directory)
                self.assertFalse(list(Path(directory).rglob("*.exe")))
                self.assertFalse(list(Path(directory).rglob("*.part")))

    def test_inconsistent_github_digest(self):
        value = release()
        value["assets"][0]["digest"] = "sha256:" + "b" * 64
        update = parse_release(value)
        with tempfile.TemporaryDirectory() as directory, patch("updater.read_small", return_value=f"{'a' * 64}  {update.name}".encode()):
            with self.assertRaises(ValueError):
                download_update(update, directory)

    def test_no_release_is_normal(self):
        error = urllib.error.HTTPError("url", 404, "Not found", {}, None)
        with patch("updater.read_small", side_effect=error):
            self.assertIsNone(check_update())

    def test_rate_limit_actionable(self):
        error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with patch("updater.read_small", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "GitHub"):
                check_update()
