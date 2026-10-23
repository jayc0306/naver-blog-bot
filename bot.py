"""텔레그램 승인 봇. 매일 아침 후보 → 선택 → 초안 → 제목 선택 → 붙여넣기용 파일."""
import asyncio
import logging
import os
import random
import re
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, InputMediaDocument, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import cards
import collect
import generate
import store

load_dotenv(Path(__file__).parent / ".env", encoding="utf-8-sig")
BASE = Path(__file__).parent
# python bot.py presets/xxx.yaml 처럼 설정 파일을 직접 줄 때만 인자로 인식 (actions_run.py daily 같은 인자는 무시)
_arg_cfg = next((a for a in sys.argv[1:] if a.endswith((".yaml", ".yml"))), None)
CONFIG_PATH = Path(_arg_cfg) if _arg_cfg else BASE / "presets" / os.environ.get("PRESET", "travel.yaml")
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
TZ = ZoneInfo(CFG.get("timezone", "Asia/Seoul"))
# 서버에서는 DATA_ROOT(예: /data, 영구 저장 볼륨)를 쓰고, PC에서는 프로그램 폴더 안 data/ 를 쓴다
_data_rel = Path(CFG.get("data_dir", "data/default"))
DATA = Path(os.environ["DATA_ROOT"]) / _data_rel.name if os.environ.get("DATA_ROOT") else BASE / _data_rel
DATA.mkdir(parents=True, exist_ok=True)
store.DB = DATA / "data.db"
OUT = DATA / "output"
ACTIONS_MODE = bool(os.environ.get("ACTIONS_MODE"))
_nid, _nsec = os.environ.get("NAVER_CLIENT_ID", "").strip(), os.environ.get("NAVER_CLIENT_SECRET", "").strip()
NAVER = (_nid, _nsec) if _nid and _nsec else None  # 없으면 구글 뉴스로 대체, 트렌드 정렬 생략


def get_news(query: str, display: int = 10, max_age_hours: int = 48) -> list[dict]:
    if NAVER:
        return collect.fetch_news(query, *NAVER, display=display, max_age_hours=max_age_hours)
    return collect.fetch_news_google(query, display=display, max_age_hours=max_age_hours)
_cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CHAT_ID = int(_cid) if _cid.lstrip("-").isdigit() else None

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # 봇 토큰이 URL에 찍히는 것 방지
log = logging.getLogger("blogbot")


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def font_path() -> str:
    p = Path(CFG.get("font_path", ""))
    return str(p if p.is_absolute() else BASE / p)


# 사용자가 붙여넣은 크리에이터 어드바이저 '메인(홈판) 유입 트렌드' 제목들 (actions_run 이 채움)
TREND_TITLES: list[str] = []


def style_notes() -> str:
    p = BASE / CFG.get("style_notes", "style_notes.md")
    notes = p.read_text(encoding="utf-8") if p.exists() else ""
    if TREND_TITLES:
        notes += ("\n\n## 지금 네이버 홈판에서 유입이 많은 글 제목 (크리에이터 어드바이저 메인 트렌드)\n"
                  "제목의 길이·말투·후킹 방식을 참고한다. 문장을 베끼지는 않는다.\n"
                  + "\n".join(f"- {t}" for t in TREND_TITLES[:30]))
    return notes


def chunks(text: str, size: int = 3900):
    """텔레그램 4096자 제한에 맞춰 줄 단위로 자르고, 너무 긴 줄은 강제로 자른다."""
    buf = ""
    for line in text.split("\n"):
        while len(line) > size:
            if buf.strip():
                yield buf
            buf = ""
            yield line[:size]
            line = line[size:]
        if len(buf) + len(line) + 1 > size:
            yield buf
            buf = ""
        buf += line + "\n"
    if buf.strip():
        yield buf


def allowed(update: Update) -> bool:
    return CHAT_ID is not None and update.effective_chat and update.effective_chat.id == CHAT_ID


# ---------------------------------------------------------------- 파이프라인 (동기, 스레드에서 실행)

def build_candidates() -> list[tuple[int, dict]]:
    # 1) 시드별 최신 뉴스
    news = {}
    for seed in CFG["seed_keywords"]:
        try:
            items = get_news(seed, display=CFG["news_per_seed"], max_age_hours=CFG.get("news_max_age_hours", 24))
        except Exception:
            log.exception("news failed: %s", seed)
            items = []
        if items:
            news[seed] = items
    # 0) 오늘 홈판 메인 트렌드 (사용자가 붙여넣은 것)
    if TREND_TITLES:
        news = {"홈판 메인 트렌드(오늘)": [{"title": t, "desc": "네이버 홈판에서 지금 유입 많은 글", "url": f"trend:{i}",
                                          "naver_link": ""} for i, t in enumerate(TREND_TITLES[:30])], **news}

    # 1-0) 다가오는 축제·행사 (관광공사)
    fest = fetch_fest()
    if fest:
        news = {"다가오는 축제·행사(관광공사)": fest, **news}
    log.info("festivals: %d", len(fest))

    # 1-1) 정부 보도자료 (공식 출처)
    press = fetch_press()
    if press:
        news = {"정부 보도자료": press, **news}
    log.info("press releases: %d", len(press))
    if not news:
        return []

    # 2) Claude가 롱테일 후보 + 핵심 고유명사(entity) 추출
    topics = generate.propose_topics(
        CFG["model"], CFG["category"], news, CFG["candidate_pool"],
        store.recent_keywords(30), CFG.get("blocklist", []), style_notes(), CFG.get("topic_rules", ""),
    )
    if not topics:
        return []

    # 3) entity 검색량 상승률로 정렬 (데이터랩 실패 시 Claude 순서 유지)
    entities = list(dict.fromkeys(t["entity"] for t in topics))
    try:
        scores = collect.trend_scores(entities, *NAVER, today=datetime.now(TZ).date()) if NAVER else {}
    except Exception:
        log.exception("datalab failed")
        scores = {}
    for t in topics:
        t["score"] = scores.get(t["entity"], 1.0)
    topics.sort(key=lambda t: t["score"], reverse=True)
    log.info("ranked: %s", [(t["entity"], round(t["score"], 2)) for t in topics])

    day = today()
    return [(store.add_topic(day, t), t) for t in topics[: CFG["candidates_per_run"]]]


def fetch_press() -> list[dict]:
    if not (CFG.get("use_press_releases") and os.environ.get("DATA_GO_KR_KEY")):
        return []
    try:
        return collect.fetch_press_releases(
            os.environ["DATA_GO_KR_KEY"], CFG["press_endpoint"], datetime.now(TZ).date(),
            CFG.get("press_days", 2), CFG.get("press_ministries"), CFG.get("press_max_items", 30),
        )
    except Exception:
        log.exception("press releases failed")
        return []


def fetch_fest() -> list[dict]:
    if not (CFG.get("use_festivals") and os.environ.get("DATA_GO_KR_KEY")):
        return []
    try:
        return collect.fetch_festivals(os.environ["DATA_GO_KR_KEY"], CFG["festival_endpoint"],
                                       datetime.now(TZ).date(), CFG.get("festival_days_ahead", 14))
    except Exception:
        log.exception("festivals failed")
        return []


def manual_topic(text: str) -> dict:
    """사용자가 직접 준 글감 → 관련 보도자료·관광공사 행사정보를 붙여서 주제로 만든다."""
    words = [w for w in re.split(r"\s+", text) if len(w) >= 2]
    pool = fetch_fest() + fetch_press()
    press = [p for p in pool if any(w in p["title"] or w in p.get("body", "") for w in words)]
    return {"keyword": text, "entity": text.split()[0], "angle": "", "why_now": "",
            "seed": "직접 입력", "sources": press[:3]}


REVIEW_SEED = "후기 메모"
DEFAULT_REVIEW_RULES = """- 글쓴이가 직접 다녀온 후기다. 메모에 있는 경험·시간·숫자·가격·감정을 그대로 살리고, 메모에 없는 경험은 절대 지어내지 않는다.
- 구조: 장소명과 한 줄 요약 → 시간 순서 소제목(도착·대기 → 입장 → 메뉴·구매 → 가격 → 총평).
- 1인칭 구어체(~했어요, ~더라구요). 솔직한 한마디는 살리되 과장하지 않는다.
- 마지막은 다시 갈지, 누구에게 추천하는지 솔직한 결론.
- 영업시간·주소 같은 매장 정보는 확인된 것만.
- 이미지는 photo(글쓴이가 찍은 사진 자리, 메모에 나온 장면 위주)와 card(핵심 숫자 1~2개)만. aiimg는 쓰지 않는다."""


def build_draft(topic: dict) -> dict:
    if topic.get("seed") == REVIEW_SEED:  # 내 경험 메모로 쓰는 후기 글
        return generate.write_draft(CFG["model"], CFG["category"], topic, topic["sources"], style_notes(),
                                    CFG.get("review_rules", DEFAULT_REVIEW_RULES),
                                    "photo, card, place, link 블록만 사용. aiimg 금지.",
                                    CFG.get("web_search", True), 0, CFG.get("title_mode", "homefeed"))
    articles, seen = [], set()
    topic["sources"].sort(key=lambda a: not a.get("official"))  # 보도자료를 맨 앞에
    try:
        extra = get_news(topic["keyword"], display=10, max_age_hours=24 * 7)
    except Exception:
        log.exception("extra news failed")
        extra = []
    for a in topic["sources"] + extra:
        if a["url"] in seen:
            continue
        seen.add(a["url"])
        articles.append(dict(a))
    fetched = 0
    for a in articles:
        if fetched >= 4:
            break
        body = collect.fetch_article_text(a.get("naver_link", ""))
        if body:
            a["body"] = body
            fetched += 1
    articles = articles[:8]
    return generate.write_draft(CFG["model"], CFG["category"], topic, articles, style_notes(),
                                CFG.get("draft_rules", ""), CFG.get("media_rule", ""),
                                CFG.get("web_search", True), CFG.get("me_blocks", 1),
                                CFG.get("title_mode", "homefeed"))


# ---------------------------------------------------------------- 텔레그램 출력

async def send_candidates(bot) -> None:
    await bot.send_message(CHAT_ID, "🔍 오늘 후보 수집 중...")
    try:
        cands = await asyncio.to_thread(build_candidates)
    except Exception as e:
        log.exception("candidates failed")
        await bot.send_message(CHAT_ID, f"⚠️ 후보 수집 실패: {e}")
        return
    if not cands:
        await bot.send_message(CHAT_ID, "오늘은 쓸 만한 후보가 없어요. /run 으로 다시 시도하거나 config.yaml 시드를 바꿔보세요.")
        return

    done = store.count_day(today(), ("approved", "published"))
    lines = [f"📋 후보 {len(cands)}개 · 오늘 {done}/{CFG['posts_per_day']} 진행\n"]
    rows = []
    for i, (tid, t) in enumerate(cands, 1):
        trend = (f"  (🔥 검색 {t['score']:.1f}배)" if t.get("score", 1) > 1.2 else "  (검색량 평이)") if NAVER else ""
        lines.append(f"{i}. {t['keyword']}{trend}\n   ↳ {t.get('angle', '')}\n   ↳ {t.get('why_now', '')}\n")
        rows.append([Btn(f"{i}. {t['keyword'][:28]}", callback_data=f"pick:{tid}")])
    await bot.send_message(CHAT_ID, "\n".join(lines), reply_markup=Kb(rows))


async def send_draft(bot, tid: int) -> None:
    topic = store.get_topic(tid)
    await bot.send_message(CHAT_ID, f"✍️ '{topic['keyword']}' 초안 작성 중... (1분 내외)")
    try:
        draft = await asyncio.to_thread(build_draft, topic)
    except Exception as e:
        log.exception("draft failed")
        store.set_topic_status(tid, "candidate")
        await bot.send_message(CHAT_ID, f"⚠️ 초안 실패: {e}\n후보 버튼을 다시 누르면 재시도해요.")
        return
    did = store.save_draft(tid, today(), draft)
    if draft.get("_notice"):
        await bot.send_message(CHAT_ID, draft["_notice"])

    preview = generate.render(draft, 0).split("\n", 1)[1]  # 제목 줄 빼고 본문만
    for part in chunks(preview):
        await bot.send_message(CHAT_ID, part)

    titles = "\n".join(f"{i + 1}. [{t['type']}] {t['text']}" for i, t in enumerate(draft["titles"]))
    rows = [[Btn(f"제목 {i + 1}", callback_data=f"title:{did}:{i}") for i in range(len(draft["titles"]))]]
    rows.append([Btn("🔄 다시 쓰기", callback_data=f"regen:{did}"), Btn("🗑 폐기", callback_data=f"drop:{did}")])
    await bot.send_message(CHAT_ID, f"🏷 제목 후보\n{titles}\n\n제목을 고르면 최종본이 나와요.", reply_markup=Kb(rows))


def suggest_publish_time(n: int = 0) -> str:
    """지금 이후로 가장 가까운 발행 시간대 안의 시각 (분은 랜덤). 오늘 창이 다 지났으면 내일 첫 창."""
    windows = CFG.get("publish_windows", [["07:30", "09:30"], ["12:00", "13:30"]])
    now = datetime.now(TZ)
    soon = now + timedelta(minutes=10)

    def at(day_shift: int, hm: str):
        h, m = map(int, hm.split(":"))
        return (now + timedelta(days=day_shift)).replace(hour=h, minute=m, second=0, microsecond=0)

    for shift in (0, 1):
        for lo, hi in windows:
            start, end = at(shift, lo), at(shift, hi)
            if end <= soon:
                continue
            start = max(start, soon)
            t = start + timedelta(minutes=random.randint(0, max(int((end - start).total_seconds() // 60), 0)))
            wd = "월화수목금토일"[t.weekday()]
            return ("지금 바로 · " if t - now < timedelta(minutes=40) else "") + f"{t.month}/{t.day}({wd}) {t:%H:%M}"
    return "내일 오전"


async def send_img(bot, path: Path, caption: str) -> None:
    try:
        await bot.send_photo(CHAT_ID, photo=path.read_bytes(), caption=caption)
    except Exception:
        log.exception("send_photo failed, sending as file")
        await bot.send_document(CHAT_ID, document=path, filename=path.name, caption=caption)


async def send_pause():
    await asyncio.sleep(0.35)  # 텔레그램 연속 전송 제한 회피


async def finalize(bot, did: int, idx: int) -> None:
    d = store.get_draft(did)
    if d["status"] != "pending":
        await bot.send_message(CHAT_ID, "이미 처리된 초안이에요.")
        return
    topic = store.get_topic(d["topic_id"])
    official = [a for a in topic["sources"] if a.get("official")]
    footer = "\n".join(
        dict.fromkeys(f"출처: {a.get('ministry') or '정부'} {a.get('kind', '보도자료')}" + (f" ({a['url']})" if a.get("kind", "보도자료") == "보도자료" else "") for a in official)
    )
    draft = d["body"]
    title = draft["titles"][idx]["text"]

    # PC용 파일도 저장은 해둔다
    folder = OUT / d["day"]
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w가-힣]+", "_", topic["keyword"]).strip("_")[:30]
    path = folder / f"{did:04d}_{slug}.txt"
    path.write_text(generate.render(draft, idx, footer) + "\n\n" + "=" * 30 + "\n"
                    + generate.render_checklist(draft), encoding="utf-8")

    try:
        imgs = await asyncio.to_thread(
            cards.make_cards, draft, folder, f"{did:04d}", font_path(),
            CFG.get("font_index", 0), CFG.get("card_colors", ["#111111", "#FFD400"]),
            CFG.get("card_label", ""),
        )
    except Exception as e:
        log.exception("cards failed")
        await bot.send_message(CHAT_ID, f"⚠️ 카드 이미지 생성 실패: {e}")
        imgs = []
    thumb = imgs[0] if imgs else None
    card_imgs = imgs[1:]

    store.update_draft(did, status="approved", title_idx=idx, file_path=str(path))
    store.set_topic_status(d["topic_id"], "done")
    n = store.count_day(d["day"], ("approved", "published"))

    # ---- 블로그 앱에 넣는 순서 그대로 전송
    await bot.send_message(
        CHAT_ID,
        "━━━━━━━━━━━━━━\n📱 아래부터 순서대로\n"
        "글자는 꾹 눌러 복사 → 붙여넣기\n사진은 눌러서 저장 → 사진 추가\n━━━━━━━━━━━━━━",
    )
    await send_pause()
    await bot.send_message(CHAT_ID, title)
    await send_pause()
    if thumb:
        await send_img(bot, thumb, "⭐ 대표 이미지 (맨 위에 넣고 대표로 지정)")
        await send_pause()

    for seg in generate.segments(draft, idx, footer):
        kind = seg[0]
        if kind == "text":
            for part in chunks(seg[1]):
                await bot.send_message(CHAT_ID, part, disable_web_page_preview=True)
                await send_pause()
        elif kind == "card":
            no, text = seg[1], seg[2]
            if no <= len(card_imgs):
                await send_img(bot, card_imgs[no - 1], f"🖼 카드 {no:02d}")
            else:
                await bot.send_message(CHAT_ID, f"🖼 카드 {no:02d} 자리 (이미지 없음): {text}")
            await send_pause()
        elif kind == "photo":
            await bot.send_message(CHAT_ID, f"📷 여기에 직접 캡처한 사진: {seg[1]}")
            await send_pause()
        elif kind == "embed":
            await bot.send_message(CHAT_ID, f"▶️ 여기에 영상 링크: {seg[1]}")
            await send_pause()
        elif kind == "aiimg":
            await bot.send_message(CHAT_ID, "🎨 여기에 AI 이미지 ↓ 아래 프롬프트를 ChatGPT·Gemini에 붙여넣어 만들고 저장해서 넣기 (에디터 'AI 활용' 표시)")
            await send_pause()
            await bot.send_message(CHAT_ID, seg[1])
            await send_pause()
        elif kind == "place":
            await bot.send_message(CHAT_ID, f"📍 여기에 지도: 에디터의 '장소' 버튼 → '{seg[1]}' 검색해서 추가")
            await send_pause()
        elif kind == "link":
            label, url = seg[1], seg[2]
            await bot.send_message(CHAT_ID, f"🔗 여기에 '{label}' 링크 ↓ 아래 주소를 붙여넣으면 카드로 바뀌어요")
            await send_pause()
            await bot.send_message(CHAT_ID, url)
            await send_pause()
        elif kind == "tags":
            await bot.send_message(CHAT_ID, "🏷 아래 태그를 복사해서 에디터 '태그' 칸에 붙여넣기 (쉼표로 구분돼 있어요)")
            await send_pause()
            await bot.send_message(CHAT_ID, seg[1])
            await send_pause()

    await bot.send_message(
        CHAT_ID,
        "━━━━━━━━━━━━━━\n"
        f"⏰ 발행 추천: {suggest_publish_time(n)} 전후 (오전 7시~오후 7시 사이, 발행 후 1~2시간 반응이 중요)\n\n"
        + generate.render_checklist(draft)
        + f"\n\n📊 오늘 {n}/{CFG['posts_per_day']}",
        reply_markup=Kb([[Btn("✅ 발행/예약 완료", callback_data=f"pub:{did}")]] if ACTIONS_MODE else
                        [[Btn("✅ 발행/예약 완료", callback_data=f"pub:{did}"),
                          Btn("💻 PC용 파일", callback_data=f"pcfile:{did}")]]),
        disable_web_page_preview=True,
    )
    if ACTIONS_MODE:  # 서버에는 파일이 남지 않아서 PC용 파일을 바로 같이 보낸다
        await bot.send_document(CHAT_ID, document=path, filename=path.name)


async def send_pc_files(bot, did: int) -> None:
    d = store.get_draft(did)
    if not d or not d.get("file_path"):
        return
    path = Path(d["file_path"])
    await bot.send_document(CHAT_ID, document=path, filename=path.name)
    imgs = sorted(path.parent.glob(f"{did:04d}_*.png"))
    for i in range(0, len(imgs), 10):
        await bot.send_media_group(CHAT_ID, [InputMediaDocument(p.read_bytes(), filename=p.name) for p in imgs[i:i + 10]])


# ---------------------------------------------------------------- 핸들러

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    log.info("/start from chat id %s", cid)
    if not allowed(update):
        await update.message.reply_text(
            f"chat id: {cid}\n.env 의 TELEGRAM_CHAT_ID= 뒤에 이 숫자를 넣고 저장한 뒤, 검은 창을 닫고 start.bat 을 다시 실행하세요."
        )
    else:
        await update.message.reply_text(
            f"준비됨. 매일 {', '.join(CFG['schedule_times'])} 에 알림이 와요.\n"
            "· 글감을 그냥 한 줄로 보내면 → 바로 초안\n· /run → 봇이 후보 추천\n· /today → 오늘 현황"
        )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await send_candidates(context.bot)


async def start_manual(bot, text: str) -> None:
    text = text.strip()[:60]
    if len(text) < 2:
        await bot.send_message(CHAT_ID, "글감을 한 줄로 보내주세요. 예: 근로장려금 신청방법")
        return
    await bot.send_message(CHAT_ID, f"📝 '{text}' 로 진행할게요.")
    topic = await asyncio.to_thread(manual_topic, text)
    tid = store.add_topic(today(), topic)
    store.claim_topic(tid)
    await send_draft(bot, tid)


async def cmd_write(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await start_manual(context.bot, " ".join(context.args))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """명령어 없이 그냥 보낸 문장 = 글감"""
    if allowed(update) and update.message and update.message.text:
        await start_manual(context.bot, update.message.text)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        d = today()
        a = store.count_day(d, ("approved",))
        p = store.count_day(d, ("published",))
        await update.message.reply_text(f"📊 {d}\n승인 대기 발행: {a}\n발행 완료: {p}\n목표: {CFG['posts_per_day']}")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    kind, *args = q.data.split(":")
    bot = context.bot

    if kind == "pick":
        tid = int(args[0])
        if not store.claim_topic(tid):
            await bot.send_message(CHAT_ID, "이미 고른 주제예요.")
            return
        await send_draft(bot, tid)

    elif kind == "title":
        await finalize(bot, int(args[0]), int(args[1]))

    elif kind == "regen":
        d = store.get_draft(int(args[0]))
        if d["status"] != "pending":
            return
        store.update_draft(d["id"], status="discarded")
        await send_draft(bot, d["topic_id"])

    elif kind == "drop":
        d = store.get_draft(int(args[0]))
        store.update_draft(d["id"], status="discarded")
        store.set_topic_status(d["topic_id"], "dropped")
        await bot.send_message(CHAT_ID, "폐기했어요. 후보 목록에서 다른 주제를 골라주세요.")

    elif kind == "pcfile":
        await send_pc_files(bot, int(args[0]))

    elif kind == "pub":
        did = int(args[0])
        store.update_draft(did, status="published", published_at=datetime.now(TZ).isoformat())
        d = store.get_draft(did)
        n = store.count_day(d["day"], ("published",))
        await q.edit_message_reply_markup(reply_markup=Kb([[Btn("💻 PC용 파일", callback_data=f"pcfile:{did}")]]))
        await bot.send_message(CHAT_ID, f"👍 발행 기록 완료 · 오늘 발행 {n}/{CFG['posts_per_day']}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=context.error)
    if CHAT_ID is not None:
        try:
            await context.bot.send_message(CHAT_ID, f"⚠️ 오류: {context.error}")
        except Exception:
            pass


async def daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if CFG.get("daily_mode", "candidates") == "remind":
        done = store.count_day(today(), ("approved", "published"))
        await context.bot.send_message(
            CHAT_ID,
            f"☀️ 오늘 글 쓸 시간이에요 ({done}/{CFG['posts_per_day']})\n"
            "글감을 한 줄로 보내주세요. 예: 근로장려금 신청방법\n"
            "추천이 필요하면 /run",
        )
    else:
        await send_candidates(context.bot)


def main() -> None:
    store.init()
    token_env = CFG.get("telegram_token_env", "TELEGRAM_BOT_TOKEN")
    if not os.environ.get(token_env, "").strip():
        print(f"\n❌ .env 파일에 {token_env} 가 비어 있어요. .env 를 메모장으로 열어 토큰을 넣고 Ctrl+S 하세요.\n")
        sys.exit(1)
    log.info("config=%s data=%s news=%s", CONFIG_PATH.name, DATA, "naver" if NAVER else "google(fallback)")
    app = Application.builder().token(os.environ[token_env]).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("write", cmd_write))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    if CHAT_ID is not None:
        for hm in CFG["schedule_times"]:
            h, m = map(int, hm.split(":"))
            app.job_queue.run_daily(daily_job, time=dtime(h, m, tzinfo=TZ), name=f"daily-{hm}")
        log.info("daily jobs at %s %s", CFG["schedule_times"], TZ)

        # PC가 꺼져 있어서 아침 알림을 놓쳤으면, 켜진 직후 한 번 보내준다
        now = datetime.now(TZ)
        fh, fm = map(int, sorted(CFG["schedule_times"])[0].split(":"))
        missed = now.time() >= dtime(fh, fm)
        if CFG.get("daily_mode", "candidates") == "remind":
            pending = store.count_day(today(), ("approved", "published")) < CFG["posts_per_day"]
        else:
            pending = store.topics_on(today()) == 0
        if missed and pending and now.hour < 22:
            app.job_queue.run_once(daily_job, when=15, name="catch-up")
            log.info("missed today's run, catching up in 15s")
    asyncio.set_event_loop(asyncio.new_event_loop())  # 최신 파이썬(3.14+) 호환
    print("\n✅ 봇 연결 완료. 텔레그램에서 /start 를 보내보세요. (이 창은 닫지 마세요)\n", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
