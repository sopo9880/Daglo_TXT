import hashlib
from pathlib import Path
root = Path(__file__).resolve().parents[1] / "release"
lines = []
for path in sorted(root.iterdir()):
    if path.suffix.lower() in {".exe", ".zip"}:
        with path.open("rb") as file:
            lines.append(f"{hashlib.file_digest(file, 'sha256').hexdigest()}  {path.name}")
(root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
