# Daglo TXT

다글로 계정에서 접근 가능한 폴더별 녹취록을 TXT로 저장하고 ZIP으로 묶는 Windows 10/11 x64 앱입니다.

## 설치

[최신 릴리스](https://github.com/sopo9880/Daglo_TXT/releases/latest)에서 `DagloTXT-Setup-버전.exe`를 실행하세요. Python과 Chromium이 포함되어 별도 개발 환경 설치가 필요 없습니다. 시작 메뉴에 Daglo TXT가 등록되며, 바탕화면 바로가기는 설치 중 선택할 수 있습니다.

포터블 ZIP을 사용할 경우 **폴더 전체를 압축 해제**하고 `DagloTXT.exe`를 실행하세요. 포터블 버전의 업데이트도 설치형 앱으로 전환합니다. ZIP으로 계속 쓰려면 릴리스의 새 포터블 ZIP을 직접 받으세요.

## 사용 순서

1. `로그인 브라우저 열기`를 누릅니다.
2. 열린 다글로 브라우저에서 로그인하고 **그 창을 닫습니다**.
3. `폴더 스캔`을 누르고 수집할 폴더를 체크합니다.
4. `선택 폴더 수집`을 누릅니다.

기본 브라우저는 앱에 포함된 Chromium입니다. Chrome/Edge도 선택할 수 있습니다. 브라우저를 바꾸면 다시 로그인해야 할 수 있습니다. 수집할 때는 브라우저 창을 닫지 마세요.

TXT 본문은 다글로의 `스크립트 전체 복사` 결과만 저장합니다. 제목·URL·수집 상태는 `manifest.json`에 따로 기록합니다. 같은 제목의 파일이 있으면 이름 뒤에 보드 ID와 번호를 붙여 기존 파일을 보존합니다. 실패한 항목은 가짜 TXT를 만들지 않고 로그와 디버그 HTML로 남깁니다.

`기존 결과에 새 녹취록 추가`에서 기존 결과의 `manifest.json`을 선택하면 같은 폴더를 재스캔해 manifest에 없는 새 보드만 추가합니다. 이전 실패 항목을 다시 수집하는 기능은 아닙니다. 기존 manifest를 자동 백업하고 ZIP을 갱신합니다. 항목마다 manifest를 저장하므로 중단되더라도 완료한 항목의 기록이 남습니다.

## 앱 업데이트

- 시작할 때 GitHub의 최신 정식 릴리스를 확인합니다. 시작 시 확인은 체크박스로 끌 수 있습니다.
- `업데이트 확인` 버튼으로 언제든 수동 확인할 수 있습니다.
- 새 버전의 변경 사항을 확인하고 `다운로드 및 설치`, `나중에`, `이 버전 건너뛰기`를 선택합니다.
- 다운로드 크기와 SHA-256을 검사한 뒤 **일반 설치 화면**을 엽니다. 설치 진행이나 취소를 직접 선택할 수 있습니다.
- 수집 중에는 앱 업데이트를 시작할 수 없습니다.
- 다운로드가 실패하면 앱을 그대로 사용하고 다시 시도할 수 있습니다.
- 프리릴리스·초안·이전 버전은 자동 업데이트 대상이 아닙니다. 설치 프로그램도 이전 버전 설치를 차단합니다.

해시는 다운로드 손상 검증용이며 코드 서명은 아닙니다. 코드 서명 인증서는 포함되어 있지 않습니다.

## 설정과 결과 위치

설정, 전용 로그인 프로필, 오류 로그, 기본 수집 결과는 `%LOCALAPPDATA%\DagloTXT`에 저장합니다.

```text
%LOCALAPPDATA%\DagloTXT\config.json
%LOCALAPPDATA%\DagloTXT\profiles\daglo_chrome_profile
%LOCALAPPDATA%\DagloTXT\output
%LOCALAPPDATA%\DagloTXT\last_error.log
```

저장 폴더는 앱에서 변경할 수 있습니다. 업데이트와 앱 제거는 이 사용자 데이터 폴더를 삭제하지 않습니다. 이전 앱과 같은 위치에 있는 `config.json`은 최초 실행 시 가져오며 상대 경로를 원래 위치의 절대 경로로 바꿉니다. 다른 폴더에 있던 이전 수집 결과는 `기존 결과에 새 녹취록 추가`로 선택하세요.

## 개발 및 릴리스

Python 3.12 x64와 Inno Setup 6.7.3을 사용합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .venv\Scripts\python.exe
```

빌드는 Chromium을 `browsers`에 다운로드하고 테스트 → PyInstaller 폴더 번들 → 패키징된 GUI/브라우저 실행 검사 → Inno Setup 설치 프로그램 → 포터블 ZIP → `SHA256SUMS.txt`를 생성합니다. 결과는 `release`에 있습니다.

GitHub Actions는 `main`의 `version.py` 변경 시 동일하게 빌드하고 정식 릴리스를 자동 게시합니다. 다음 업데이트는 `version.py`의 버전을 올리고 `RELEASE_NOTES.md`를 수정해 main에 반영하세요. 같은 버전은 재게시하지 않습니다. Actions의 `Build and release Windows app`에서 수동 실행도 가능합니다. **GitHub Actions가 활성화되어 있고 GITHUB_TOKEN의 contents 쓰기가 허용되어야 합니다.**

```powershell
python -m unittest discover -s tests -v
```

테스트는 업데이트 버전 비교·비정상 주소·다운로드 손상·해시 검증·설정 보존·파일 충돌·신규 manifest 동기화·ZIP 실패 시 보존을 검증합니다. 패키징 검사는 GUI 생성과 내장 Chromium의 로컬 HTML 실행을 확인합니다. 실제 다글로 로그인과 계정의 녹취록 수집은 별도로 확인해야 합니다.

## 문제 해결

로그인 창을 닫은 후 스캔하세요. 동시에 앱을 여러 개 실행할 수 없으며, Chrome/Edge의 전용 프로필 창이 열려 있으면 브라우저 시작이 실패할 수 있습니다. 오류 상세는 `last_error.log`, 수집 실패 HTML은 결과 폴더의 `_debug_failed_html`에 남습니다. 디버그 HTML에는 계정 자료가 들어갈 수 있으므로 공개 저장소에 올리지 마세요.

이 앱은 다글로의 공식 앱이 아닙니다. 본인 계정으로 접근 가능한 자료의 개인 백업용입니다.

## 폴더 선택과 화면

폴더 목록에서 이름 오름차순, 이름 내림차순, 선택한 폴더 먼저 정렬을 선택할 수 있습니다. 숫자는 자연스럽게 정렬하며 정렬 기준은 다음 실행에도 유지됩니다. 이름 검색으로 목록을 좁혀도 기존 선택은 유지됩니다. 상단에는 전체 대비 선택 개수, 수집 버튼에는 선택한 개수가 표시됩니다. 전체 선택과 선택 해제는 검색으로 숨겨진 폴더에도 적용됩니다.

문서와 음성 파형을 결합한 새 앱 아이콘과 네이비·민트 UI를 적용했습니다. 긴 폴더 이름은 줄바꿈되며 이름을 눌러 선택할 수 있습니다.
