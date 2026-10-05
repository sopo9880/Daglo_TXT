import unittest
from types import SimpleNamespace
from folder_view import folder_key, visible_folders


def folder(name, key):
    return SimpleNamespace(name=name, folder_id=key, url="https://daglo.ai/board?folderIds=" + key)


class FolderViewTests(unittest.TestCase):
    def setUp(self):
        self.folders = [folder("강의 10", "ten"), folder("강의 2", "two"), folder("강의 1", "one")]

    def test_natural_ascending_and_descending(self):
        self.assertEqual([f.name for f in visible_folders(self.folders, set(), "이름 오름차순")], ["강의 1", "강의 2", "강의 10"])
        self.assertEqual([f.name for f in visible_folders(self.folders, set(), "이름 내림차순")], ["강의 10", "강의 2", "강의 1"])

    def test_selected_first_with_natural_order(self):
        self.assertEqual([folder_key(f) for f in visible_folders(self.folders, {"ten"}, "선택한 폴더 먼저")], ["ten", "one", "two"])

    def test_filter_does_not_mutate_selection_or_original_order(self):
        selected = {"ten", "one"}
        result = visible_folders(self.folders, selected, "이름 내림차순", "  강의 2  ")
        self.assertEqual([folder_key(f) for f in result], ["two"])
        self.assertEqual(selected, {"ten", "one"})
        self.assertEqual(folder_key(self.folders[0]), "ten")

    def test_unicode_case_insensitive_filter_and_empty(self):
        folders = [folder("ＡBC 2", "a"), folder("abc 10", "b")]
        self.assertEqual(len(visible_folders(folders, set(), "이름 오름차순", "abc")), 2)
        self.assertEqual(visible_folders(folders, set(), "이름 오름차순", "없는 폴더"), [])

    def test_duplicate_names_keep_distinct_ids(self):
        folders = [folder("강의", "b"), folder("강의", "a")]
        self.assertEqual([folder_key(f) for f in visible_folders(folders, {"b"}, "선택한 폴더 먼저")], ["b", "a"])
