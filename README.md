# 네이버 블로그 반자동 봇 · 여행·축제 정보 (하루 2개)

Windows에서 편집기 입력·임시저장을 연결하는 초기 버전은 [NAVER_DRAFT_WINDOWS.md](NAVER_DRAFT_WINDOWS.md)를 참고하세요. 실제 네이버 화면 호환성은 로그인한 PC에서 추가 검증이 필요합니다.

기본 프리셋: `presets/travel.yaml`. 생활경제로 돌리려면 `.env`에 `PRESET=economy.yaml`.

소재 수집과 초안은 자동, 고르고 다듬고 발행하는 건 사람. 발행 자동화는 일부러 뺐다 (매크로 발행은 네이버 제재 대상).

## 하루 흐름 (폰으로 끝)

1. **07:30** 텔레그램으로 후보 6개 도착
2. 2개 탭 → 각각 1분 안에 초안 + 제목 5개
3. 제목 탭 → **블로그에 넣는 순서 그대로** 메시지가 쭉 옴: 제목 → 대표 이미지 → 본문 조각 → 카드 → 본문 조각 → … → 태그
4. 네이버 블로그 앱에서 위에서부터: 글자는 꾹 눌러 복사·붙여넣기, 사진은 저장 후 추가
5. ✍️ 직접 쓰기 2곳 채우기 → 마지막 메시지의 **⏰ 예약발행 추천 시각**으로 예약
6. **✅ 발행/예약 완료** 탭. PC에서 하고 싶으면 **💻 PC용 파일** 탭

## 윈도우 빠른 시작
`setup.bat` 더블클릭 (설치 + 키 파일 열기) → 키 입력 후 저장 → `start.bat` 더블클릭 (봇 실행)

## 세팅 (20분)

**1. 네이버 API (선택)** — 없으면 구글 뉴스로 대신 수집하고 검색량 순위만 빠짐. developers.naver.com → 애플리케이션 등록 → 사용 API에 `검색`, `데이터랩(검색어트렌드)` 추가

**2. 공공데이터포털** — data.go.kr에서 `문화체육관광부_정책브리핑_보도자료_API` 활용신청 (자동승인, 하루 1,000건). 마이페이지의 **일반 인증키(Decoding)** 를 `DATA_GO_KR_KEY`에.
   ⚠️ 활용신청 페이지의 '요청주소'가 `presets/economy.yaml`의 `press_endpoint`와 다르면 그 주소로 바꿀 것. 키가 없거나 API가 실패해도 뉴스만으로 계속 돌아감 (텔레그램 로그에 표시).

**3. Anthropic API 키** — console.anthropic.com

**4. 텔레그램 봇** — @BotFather → `/newbot` → 토큰

**5. 실행**
```bash
pip install -r requirements.txt
cp .env.example .env      # 키 입력, TELEGRAM_CHAT_ID는 비워둠
python bot.py             # 기본 = presets/economy.yaml
```
봇에 `/start` → chat id를 `.env`에 넣고 재시작.

**6. 상시 실행** — 맥이면 `tmux`나 `caffeinate -i python bot.py`, 아니면 작은 VPS.

## 명령어
- `/run` 지금 후보 받기
- `/today` 오늘 현황

## 프리셋 (다계정 확장용)

```
presets/economy.yaml        1호기 생활경제 (기본)
presets/entertainment.yaml  2호기 방송연예 (대기)
```
프리셋마다 DB·output·스타일노트·텔레그램 봇이 분리된다. 1호기가 자리 잡으면 BotFather로 봇을 하나 더 만들어 `TELEGRAM_BOT_TOKEN_ENT`에 넣고:
```bash
python bot.py presets/entertainment.yaml
```

## 튜닝 포인트

- `seed_keywords`, `press_ministries`: 수집 범위
- `topic_rules`, `draft_rules`, `media_rule`: 주제·글·이미지 규칙
- `style_notes/economy.md`: 프롬프트에 그대로 들어감. 크리에이터 어드바이저 TOP20에서 터진 글의 교집합(제목 유형, 후킹 문구)을 찾을 때마다 추가.
- `font_path` / `card_colors`: 카드 디자인. Pretendard ExtraBold 같은 굵은 폰트 추천.

## 지켜야 할 것

- **직접 쓰기 2곳은 꼭 채울 것.** AI 초안 그대로 발행하면 네이버가 말하는 "생성형 AI 대량 자동화" 패턴에 가까워짐.
- **정부 인포그래픽·카드뉴스 원본은 쓰지 않기.** 정책브리핑은 자료 사용 전 담당자 협의를 요구하고, 부처 자료도 공공누리 유형이 자료마다 달라서(상업 이용 금지 유형 포함) 애드포스트 블로그에선 확인 없이 쓰면 위험. 자동 카드 + 직접 캡처로 대체.
- **금액·대상·기간은 팩트체크 목록의 보도자료 링크로 확인.** 보도자료와 뉴스가 다르면 보도자료 기준.
- 발행 시간은 매일 같은 분 단위로 고정하지 말 것.

## 파일 구조
```
bot.py           텔레그램 봇 + 스케줄
collect.py       보도자료 API, 데이터랩, 뉴스 검색, 네이버뉴스 본문
generate.py      Claude 주제 제안·초안·렌더링
cards.py         텍스트 카드·썸네일 생성
store.py         SQLite (30일 중복 키워드 회피, 발행 기록)
presets/         프리셋별 설정
style_notes/     프리셋별 스타일 가이드
data/<프리셋>/   DB + output(붙여넣기용 txt, 카드 png)
```

## GitHub + 24시간 서버 (Railway)

- 코드는 GitHub **비공개(Private)** 저장소에 올린다. `.env`, `data/`, `.venv/`는 `.gitignore`로 제외되어 있고, **절대 직접 올리지 않는다.**
- Railway에서 GitHub 저장소를 연결하면 `Dockerfile`로 자동 빌드·실행된다. GitHub에 파일을 올리면 자동 재배포.
- Railway 설정
  - Variables: `.env`의 키들을 그대로 입력 (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATA_GO_KR_KEY …) + `DATA_ROOT=/data`
  - Volume: 마운트 경로 `/data` (DB와 생성 파일이 재배포 후에도 유지됨)
- ⚠️ PC와 서버에서 **동시에 봇을 켜면 안 된다** (텔레그램이 한 곳에만 메시지를 줌). 서버로 옮겼으면 PC의 start.bat 자동 실행을 지울 것.
- 폰트는 `fonts/Pretendard-ExtraBold.otf` 동봉 (SIL OFL)

## GitHub Actions로 무료 실행 (PC 꺼도 됨)

상시 켜진 봇 대신, GitHub가 정해진 시간마다 잠깐 실행해서 일을 처리하고 꺼진다.

- **매일 07:30쯤**: 주제를 알아서 골라 글 2개를 **완성본으로** 텔레그램에 보냄
- **08:00~23:50, 10분마다**: 그동안 보낸 글감 한 줄, ✅ 버튼 처리
- 즉시 받고 싶으면: GitHub → Actions → blogbot → Run workflow
- GitHub 예약 실행은 몇 분~수십 분 늦게 시작될 수 있음
- 저장소는 **Public** 기준 (Actions 무제한 무료). Private으로 쓰면 월 2,000분 한도라 poll 주기를 `*/30`으로 바꿀 것
- 키는 저장소가 아니라 **Settings → Secrets**에만. 봇 기억(state)은 커밋하지 않고 Actions 캐시에 보관해서 공개되지 않음

Secrets: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DATA_GO_KR_KEY` (네이버 키는 선택)
생활경제로 바꾸려면 Variables 탭에 `PRESET` = `economy.yaml`

⚠️ PC에서 start.bat을 같이 켜두면 텔레그램 메시지를 PC가 가져가서 GitHub 쪽이 못 받는다. 둘 중 하나만.
