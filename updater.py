"""GitHub stable releases with bounded downloads and SHA-256 verification."""
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from version import APP_VERSION

REPOSITORY = "sopo9880/Daglo_TXT"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_INSTALLER_BYTES = 900 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        raise ValueError("정식 버전 번호 형식이 올바르지 않습니다.")
    return tuple(int(x) for x in match.groups())


def request(url):
    return urllib.request.Request(url, headers={"User-Agent": f"DagloTXT/{APP_VERSION}", "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "application/octet-stream"})


def read_small(url, limit=2 * 1024 * 1024):
    with urllib.request.urlopen(request(url), timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("업데이트 정보가 허용 크기를 초과했습니다.")
    return data


def validate_asset_url(url, tag, name):
    expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    if url != expected:
        raise ValueError("이 저장소의 정식 릴리스 다운로드 주소가 아닙니다.")
    return url


@dataclass(frozen=True)
class Update:
    version: str
    notes: str
    url: str
    name: str
    size: int
    checksum_url: str
    digest: str = ""


def parse_release(release, current=APP_VERSION):
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name", "")
    if version_tuple(tag) <= version_tuple(current):
        return None
    version = tag.removeprefix("v")
    name = f"DagloTXT-Setup-{version}.exe"
    assets = {x.get("name"): x for x in release.get("assets", [])}
    if name not in assets or "SHA256SUMS.txt" not in assets:
        raise ValueError("설치 파일 또는 검증 파일이 없어 업데이트할 수 없습니다.")
    asset = assets[name]
    size = asset.get("size", 0)
    if not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_BYTES:
        raise ValueError("설치 파일 크기가 올바르지 않습니다.")
    digest = asset.get("digest") or ""
    if digest and not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("릴리스 해시 형식이 올바르지 않습니다.")
    return Update(version, str(release.get("body") or "변경 사항은 릴리스 페이지에서 확인할 수 있습니다.")[:20000],
                  validate_asset_url(asset.get("browser_download_url"), tag, name), name, size,
                  validate_asset_url(assets["SHA256SUMS.txt"].get("browser_download_url"), tag, "SHA256SUMS.txt"), digest)


def check_update():
    try:
        release = json.loads(read_small(API_URL))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code in (403, 429):
            raise RuntimeError("GitHub 요청 한도에 도달했습니다. 잠시 후 다시 확인하세요.") from error
        raise
    return parse_release(release)


def checksum_for(contents, name):
    matches = []
    for line in contents.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2) == name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("설치 파일의 SHA-256 검증 정보를 찾을 수 없습니다.")
    return matches[0]


def download_update(update, cache_dir, progress=lambda done, total: None):
    expected = checksum_for(read_small(update.checksum_url, 65536).decode("utf-8"), update.name)
    if update.digest and update.digest.split(":", 1)[1].lower() != expected:
        raise ValueError("GitHub 해시와 검증 파일이 일치하지 않습니다.")
    destination = Path(cache_dir) / uuid.uuid4().hex / update.name
    destination.parent.mkdir(parents=True, exist_ok=False)
    temporary = destination.with_suffix(".part")
    try:
        digest = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(request(update.url), timeout=30) as response, temporary.open("xb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > update.size:
                    raise ValueError("다운로드 크기가 릴리스 정보와 일치하지 않습니다.")
                digest.update(chunk)
                stream.write(chunk)
                progress(count, update.size)
        if count != update.size or digest.hexdigest() != expected:
            raise ValueError("설치 파일 검증에 실패했습니다. 다시 다운로드하세요.")
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def launch_installer(path):
    # Run the normal visible installer, allowing users to inspect/cancel it.
    return subprocess.Popen([str(Path(path).resolve()), "/SP-"], close_fds=True)
