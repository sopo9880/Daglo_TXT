"""Stable folder identity and natural name ordering, independent of the GUI."""
import re
import unicodedata

SORT_OPTIONS = ("이름 오름차순", "이름 내림차순", "선택한 폴더 먼저")


def folder_key(folder):
    return folder.folder_id or folder.url or folder.name


def natural_key(name):
    normalized = unicodedata.normalize("NFKC", name).casefold()
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in re.split(r"(\d+)", normalized))


def visible_folders(folders, selected_keys, order, query=""):
    query = unicodedata.normalize("NFKC", query).casefold().strip()
    visible = [f for f in folders if query in unicodedata.normalize("NFKC", f.name).casefold()]
    if order == "선택한 폴더 먼저":
        return sorted(visible, key=lambda f: (folder_key(f) not in selected_keys, natural_key(f.name), folder_key(f)))
    return sorted(visible, key=lambda f: (natural_key(f.name), folder_key(f)), reverse=order == "이름 내림차순")
