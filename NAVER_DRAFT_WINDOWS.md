# Windows에서 네이버 임시저장까지 연결하기

원하는 흐름은 소재 선택 → AI 초안·카드 생성 → 네이버 편집기에 입력 → 임시저장 → 직접 검수·발행입니다.
기존 봇의 SQLite 초안을 가져오는 PC용 연결부를 추가했습니다. 발행·예약 버튼은 조작하지 않습니다.

현재 구현은 **실제 네이버 화면 연결을 검증하기 전의 초기 버전**입니다. Chromium의 로컬 편집 화면에서 본문·이미지 순서, 저장 후 재열기, 기존 글 보호 등을 검사했습니다. 네이버의 실제 버튼·이미지 업로드·저장 안내가 예상한 구조와 다르면 멈춥니다. 이 경우 실제 화면을 기준으로 연결부를 조정해야 합니다. Windows 배치 파일은 이 Linux 환경에서 실행하지 못했습니다.

## 1. 설치와 로그인

Microsoft Edge가 설치된 Windows PC를 기준으로 합니다. setup_naver.bat는 Python 3.12를 확인하고, 없으면 설치되어 있는 Python install manager의 py install 3.12 명령으로 설치를 시도합니다. 설치 관리자까지 없다면 python.org에서 Python 3.12와 Python launcher를 설치하세요.

1. 압축을 풀고 setup_naver.bat를 실행합니다.
2. 해당 폴더에서 명령 프롬프트(cmd)를 열고 아래 명령을 실행합니다.

    naver.bat login --blog-id 내블로그ID

내블로그ID를 실제 블로그 주소의 ID로 바꾸세요. 열리는 전용 Edge 창에서 직접 로그인한 뒤 명령 프롬프트로 돌아와 Enter를 누릅니다. 추가 인증·CAPTCHA도 직접 처리합니다. 실제 글쓰기 편집기를 찾은 경우에만 현재 세션의 로그인 접근이 확인됐다고 표시합니다. 이미 글쓰기가 열리면 다시 로그인 페이지로 보내지 않습니다. Chrome을 쓰려면 login과 save 양쪽에 --browser chrome을 붙입니다.

login 명령은 선택 사항입니다. save 실행 중 로그인 화면이 나오면 **그 전용 브라우저 창에서** 직접 로그인한 뒤 이어갈 수 있습니다. 일반 Edge/Chrome 창의 로그인과 전용 프로필의 로그인은 별개입니다. 로그인 상태 유지를 선택하면 다음 실행에서 세션이 유지되는 데 도움이 되지만, 실제 다음 실행의 글쓰기 접근 전에는 세션 유지 성공을 확정하지 않습니다.

같은 전용 창에서 로그인과 추가 인증을 완료했는데도 다시 로그인 화면으로 돌아가면, 반복 로그인은 중단합니다. 평소 사용하는 브라우저에서 직접 본인 블로그의 글쓰기가 열리는지 확인하고 현재 로그인 화면의 안내 문구를 확인하세요. 브라우저의 인증·보안 확인을 우회하지 않습니다. 프로그램은 이 상태를 임시저장 성공으로 처리하지 않으며, 실패 이유를 브라우저를 닫기 전에 먼저 표시합니다.

비밀번호를 코드나 채팅에 넣지 않습니다. 로그인 세션은 PC의 %LOCALAPPDATA%\NaverBlogBot\browser-profile에 보관합니다. 이 폴더는 로그인 권한을 포함하므로 공유·Git 업로드하지 마세요. 평소 브라우저의 사용자 프로필을 복사하지 않습니다. 같은 전용 프로필로 여러 실행을 동시에 켜지 마세요.

### No runtime installed that matches 3.12 오류

Python 실행 관리자만 있고 Python 3.12 본체가 없는 상태입니다. Windows 터미널에서 아래 두 명령을 순서대로 실행하세요.

    py install 3.12
    py -3.12 --version

두 번째 명령에 Python 3.12.x가 표시되면 setup_naver.bat를 다시 실행합니다. 기존에 다운로드한 파일도 Python 설치 후 다시 실행할 수 있습니다. 수정된 설치 파일은 가상환경의 실제 실행 파일과 버전까지 확인하며, 생성 실패 시 의존성 설치로 진행하지 않습니다.

## 2. 비용 없이 테스트 초안 준비

아래 명령은 AI API를 호출하지 않습니다. 동봉한 테스트 문구와 한글 카드만 사용합니다.

    naver.bat export --json examples\naver-draft.sample.json --out "%LOCALAPPDATA%\NaverBlogBot\sample"

생성된 폴더의 preview.html을 먼저 열어 보세요. 기존 폴더를 덮어쓰지 않으므로 다시 내보내려면 다른 출력 폴더 이름을 사용합니다.

## 3. 임시저장 연결 검증

아래 내블로그ID를 실제 블로그 주소의 ID로 바꿉니다.

    naver.bat save "%LOCALAPPDATA%\NaverBlogBot\sample\manifest.json" --blog-id 내블로그ID

1. 열리는 화면에서 대상 블로그가 맞는지 확인하고 도움말·글 복구 팝업을 직접 처리합니다. 기존 내용이 없는 빈 편집기가 필요합니다.
2. 명령 프롬프트에서 Enter를 누르면 제목·본문·카드 이미지를 순서대로 입력하고 저장 버튼을 누릅니다.
3. 저장 안내가 나타나면 검증용 새 탭이 열립니다. **그 탭의 임시저장 목록에서 방금 글을 직접 다시 열고**, 명령 프롬프트에서 Enter를 누릅니다. 이 첫 버전은 목록에서 글을 고르는 단계가 수동입니다.
4. 다시 연 제목·본문과 이미지 수를 비교합니다. 이미지의 실제 내용·배치도 화면에서 확인하세요. 테스트 글은 발행하지 마세요.

안내 문구가 예상과 다르거나 확인되지 않으면 성공으로 처리하지 않습니다. 저장 시도 기록이 프로필의 상위 폴더에 verified-해시.json 이름으로 남습니다. status가 save-attempted-unverified이면 실제 저장 여부를 아직 모르는 상태입니다. 같은 초안의 재실행을 막아 중복 글을 방지합니다. 임시저장 목록을 직접 확인하고, 저장되지 않았음을 확인한 경우에만 해당 기록을 수동으로 정리한 뒤 재시도하세요. 임시저장 전용이라는 이유만으로 계정 제재나 서비스 정책 준수 여부가 보장되지는 않습니다.

## 4. 기존 봇에서 만든 실제 초안 사용

기존 봇을 PC에서 실행해 초안이 생성된 경우:

    naver.bat export --db data\travel\data.db --draft-id 1 --out "%LOCALAPPDATA%\NaverBlogBot\draft-1"
    naver.bat save "%LOCALAPPDATA%\NaverBlogBot\draft-1\manifest.json" --blog-id 내블로그ID

DB 경로와 초안 번호는 실제 값으로 바꿉니다. DATA_ROOT를 지정했다면 그 위치의 DB를 사용하세요. DB는 읽기 전용으로 열며 기존 후보·초안 상태를 바꾸지 않습니다. 선택한 제목이 있으면 그대로 사용하고, 없으면 첫 제목을 사용합니다. 다른 제목은 export에 --title-index 0 같은 0부터 시작하는 번호를 지정합니다.

초안 생성 자체에는 기존 봇의 Anthropic 설정과 뉴스 접근이 필요합니다. 이 도구는 신규 AI 글 생성이나 트렌드 수집을 실행하지 않습니다. 텔레그램으로 받은 txt만으로는 카드·본문의 구조를 복원하지 않으므로, SQLite DB 또는 원본 초안 JSON이 필요합니다. GitHub Actions/서버의 DB가 PC에 자동 동기화되지는 않습니다. export 결과 폴더를 통째로 PC에 옮기면 생성 작업과 브라우저 작업을 분리할 수 있습니다.

카드·요약표는 실제 PNG로 올립니다. 직접 촬영할 사진, AI 이미지, 지도, 영상은 작업 안내 자리로 남깁니다. 태그 후보와 팩트체크는 preview.html에 표시하며, 발행 화면의 태그는 직접 입력합니다. 실제로 다녀오거나 사용하지 않은 경험을 만들어 넣지 않습니다.

## 개발 검증

일반 Chromium 설치 후:

    python -m pip install -r requirements-naver.txt
    python -m playwright install chromium
    python -m unittest discover -s tests -v

이 클라우드 환경은 시스템 Chromium을 사용합니다.

    TEST_CHROMIUM_PATH=/usr/bin/chromium python -m unittest discover -s tests -v

로컬 테스트 통과가 실제 네이버 로그인·서버 임시저장 성공이나 수익·노출을 입증하지는 않습니다.
