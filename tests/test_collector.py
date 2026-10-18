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

    def test_incremental_skips_existing_txt_and_creates_flat_archive(self):
        from zipfile import ZipFile

        root = data_dir() / "output"
        folder_dir = root / "운영체제"
        folder_dir.mkdir(parents=True)
        existing = folder_dir / "1주차.txt"
        existing.write_text("old content", encoding="utf-8")
        folder = FolderInfo("운영체제", "https://daglo.ai/board?folderIds=os", "os")
        old = BoardInfo("1주차", "https://daglo.ai/board/one?fileMetaId=one", "운영체제")
        new = BoardInfo("2주차", "https://daglo.ai/board/two?fileMetaId=two", "운영체제")
        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[old, new]), patch.object(self.collector, "extract_transcript", return_value=("n" * 60, "2주차")) as extract, patch("daglo_collector.time.sleep"):
            archive = self.collector.collect_folders([folder], mode="incremental")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(existing.read_text(encoding="utf-8"), "old content")
        self.assertEqual((folder_dir / "2주차.txt").read_text(encoding="utf-8"), "n" * 60 + "\n")
        self.assertEqual(archive, root / "DagloTXT_전체.zip")
        self.assertFalse(list(root.glob("daglo_transcripts_*")))
        with ZipFile(archive) as zf:
            self.assertEqual(set(zf.namelist()), {"운영체제/1주차.txt", "운영체제/2주차.txt", "manifest.json"})
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 2)

        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[old, new]), patch.object(self.collector, "extract_transcript") as extract:
            self.collector.collect_folders([folder], mode="incremental")
        extract.assert_not_called()
        self.assertEqual(len(json.loads((root / "manifest.json").read_text(encoding="utf-8"))), 2)

    def test_incremental_imports_legacy_run_without_overwriting(self):
        from dataclasses import asdict
        from zipfile import ZipFile

        root = data_dir() / "output"
        run = root / "daglo_transcripts_20261013_113210"
        old_dir = run / "데이터베이스"
        old_dir.mkdir(parents=True)
        original = old_dir / "1강.txt"
        original.write_text("legacy content", encoding="utf-8")
        folder = FolderInfo("데이터베이스", "https://daglo.ai/board?folderIds=db", "db")
        board = BoardInfo("1강", "https://daglo.ai/board/old?fileMetaId=old", "데이터베이스")
        (run / "manifest.json").write_text(json.dumps([{
            "folder": asdict(folder), "board": asdict(board),
            "status": "ok", "saved_path": "데이터베이스/1강.txt", "error": "",
        }], ensure_ascii=False), encoding="utf-8")

        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[board]), patch.object(self.collector, "extract_transcript") as extract:
            archive = self.collector.collect_folders_incremental([folder])
        extract.assert_not_called()
        self.assertEqual(original.read_text(encoding="utf-8"), "legacy content")
        self.assertEqual((root / "데이터베이스" / "1강.txt").read_text(encoding="utf-8"), "legacy content")
        with ZipFile(archive) as zf:
            self.assertIn("데이터베이스/1강.txt", zf.namelist())
            self.assertNotIn("daglo_transcripts_20261013_113210/데이터베이스/1강.txt", zf.namelist())

    def test_incremental_same_title_different_board_keeps_both(self):
        from dataclasses import asdict

        root = data_dir() / "output"
        folder_dir = root / "과목"
        folder_dir.mkdir(parents=True)
        first_file = folder_dir / "강의.txt"
        first_file.write_text("first original", encoding="utf-8")
        folder = FolderInfo("과목", "https://daglo.ai/board?folderIds=c", "c")
        first = BoardInfo("강의", "https://daglo.ai/board/first?fileMetaId=first", "과목")
        second = BoardInfo("강의", "https://daglo.ai/board/second?fileMetaId=second", "과목")
        (root / "manifest.json").write_text(json.dumps([{
            "folder": asdict(folder), "board": asdict(first),
            "status": "ok", "saved_path": "과목/강의.txt", "error": "",
        }], ensure_ascii=False), encoding="utf-8")
        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[first, second]), patch.object(self.collector, "extract_transcript", return_value=("b" * 60, "강의")) as extract, patch("daglo_collector.time.sleep"):
            self.collector.collect_folders_incremental([folder])
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(first_file.read_text(encoding="utf-8"), "first original")
        self.assertEqual((folder_dir / "강의_second.txt").read_text(encoding="utf-8"), "b" * 60 + "\n")

    def test_incremental_missing_manifest_txt_is_retried(self):
        from dataclasses import asdict

        root = data_dir() / "output"
        root.mkdir()
        folder = FolderInfo("과목", "https://daglo.ai/board?folderIds=c", "c")
        board = BoardInfo("유실된강의", "https://daglo.ai/board/one?fileMetaId=one", "과목")
        (root / "manifest.json").write_text(json.dumps([{
            "folder": asdict(folder), "board": asdict(board),
            "status": "ok", "saved_path": "과목/유실된강의.txt", "error": "",
        }], ensure_ascii=False), encoding="utf-8")
        with patch("daglo_collector.sync_playwright"), patch.object(self.collector, "_launch_context"), patch.object(self.collector, "_detect_login_by_navigation"), patch.object(self.collector, "scan_boards_in_folder", return_value=[board]), patch.object(self.collector, "extract_transcript", return_value=("new text " * 8, "유실된강의")) as extract, patch("daglo_collector.time.sleep"):
            self.collector.collect_folders_incremental([folder])
        extract.assert_called_once()
        self.assertTrue((root / "과목" / "유실된강의.txt").is_file())

    def test_incremental_zip_failure_preserves_existing_archive(self):
        root = data_dir() / "output"
        root.mkdir()
        (root / "lecture.txt").write_text("text")
        archive = self.collector.zip_incremental_output()
        before = archive.read_bytes()
        with patch("daglo_collector.zipfile.ZipFile.write", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.collector.zip_incremental_output()
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse((root / ".DagloTXT_전체.zip.tmp").exists())

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
