import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from daglo_collector import DagloCollector, FolderInfo, BoardInfo, board_key_from_url, load_config, save_config, safe_filename
from app_paths import data_dir


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DAGLO_DATA_DIR": self.temp.name})
        self.env.start()
        self.collector = DagloCollector({"profile_dir": "profile", "output_dir": "output"}, lambda text: None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_settings_and_profiles_survive_version_change(self):
        config = {"output_dir": "custom", "skipped_update": "1.2.0", "auto_check_updates": False}
        save_config(config)
        with patch("daglo_collector.APP_VERSION", "2.0.0"):
            loaded = load_config()
        self.assertEqual(loaded["output_dir"], "custom")
        self.assertFalse(loaded["auto_check_updates"])
        self.assertEqual(self.collector.profile_dir.parent, data_dir())
        self.assertEqual(json.loads((data_dir() / "config.json").read_text()), config)

    def test_same_title_never_overwrites(self):
        folder = data_dir() / "output"
        first = self.collector._unique_transcript_path(folder, "강의", "https://daglo.ai/board/one")
        first.write_text("original", encoding="utf-8")
        second = self.collector._unique_transcript_path(folder, "강의", "https://daglo.ai/board/two")
        second.write_text("second", encoding="utf-8")
        third = self.collector._unique_transcript_path(folder, "강의", "https://daglo.ai/board/two")
        self.assertNotEqual(second, third)
        self.assertEqual(first.read_text(encoding="utf-8"), "original")

    def test_windows_reserved_names(self):
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("LPT1.txt"), "_LPT1.txt")

    def test_board_identity_ignores_query_order(self):
        self.assertEqual(board_key_from_url("https://daglo.ai/board/one?fileMetaId=f&x=1"), board_key_from_url("https://daglo.ai/board/one?x=2&fileMetaId=f"))

    def test_closed_browser_is_actionable(self):
        page = MagicMock()
        page.is_closed.return_value = True
        with self.assertRaisesRegex(RuntimeError, "닫혔습니다"):
            self.collector._page_content(page)

    def test_zip_failure_keeps_previous_archive(self):
        folder = data_dir() / "run"
        folder.mkdir()
        (folder / "lecture.txt").write_text("text")
        archive = self.collector.zip_output(folder)
        original = archive.read_bytes()
        with patch("daglo_collector.zipfile.ZipFile.write", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.collector.zip_output(folder)
        self.assertEqual(archive.read_bytes(), original)

    def test_sync_adds_only_new_without_overwriting(self):
        run = data_dir() / "run"
        (run / "course").mkdir(parents=True)
        existing = run / "course" / "강의.txt"
        existing.write_text("old transcript", encoding="utf-8")
        folder = FolderInfo("course", "https://daglo.ai/board?folderIds=f", "f")
        old = BoardInfo("강의", "https://daglo.ai/board/old?fileMetaId=old", "course")
        new = BoardInfo("강의", "https://daglo.ai/board/new?fileMetaId=new", "course")
        from dataclasses import asdict
        manifest = [{"folder": asdict(folder), "board": asdict(old), "status": "ok", "saved_path": "course/강의.txt"}]
        path = run / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[old, new]), patch.object(self.collector, "extract_transcript", return_value=("new transcript", "강의")) as extract, patch("daglo_collector.time.sleep"):
            self.collector.sync_new_from_manifest(path)
        extract.assert_called_once()
        self.assertEqual(existing.read_text(encoding="utf-8"), "old transcript")
        updated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(updated), 2)
        self.assertEqual((run / updated[1]["saved_path"]).read_text(encoding="utf-8"), "new transcript\n")
        self.assertEqual(len(list(run.glob("manifest.backup*"))), 1)
