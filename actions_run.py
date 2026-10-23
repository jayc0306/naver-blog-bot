"""GitHub Actions용 실행기. 상시 켜진 봇 대신, 정해진 시간마다 잠깐 켜져서 할 일을 처리하고 꺼진다.

  python actions_run.py daily   매일 아침: 주제를 알아서 골라 글 N개를 완성본으로 보냄
  python actions_run.py poll    30분마다: 그동안 텔레그램에 보낸 글감·버튼을 처리
"""
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

import bot  # 설정·파이프라인 재사용 (텔레그램 상시 연결은 안 씀)
import cards
import store

API = "https://api.telegram.org/bot{token}/{method}"
STATE = bot.DATA.parent / "meta.json"   # offset, 마지막 daily 날짜


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


class TG:
    """bot.py의 함수들이 쓰는 텔레그램 메서드를 HTTP로 흉내 내는 어댑터."""

    def __init__(self, token: str):
        self.token = token
        self.http = httpx.Client(timeout=60)

    def call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        url = API.format(token=self.token, method=method)
        if files:
            r = self.http.post(url, data={k: v for k, v in (data or {}).items() if v is not None}, files=files)
        else:
            r = self.http.post(url, json={k: v for k, v in (data or {}).items() if v is not None})
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"telegram {method}: {j.get('description')}")
        return j["result"]

    async def send_message(self, chat_id, text, reply_markup=None, disable_web_page_preview=None, **_):
        data = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            data["reply_markup"] = reply_markup.to_dict()
        if disable_web_page_preview:
            data["link_preview_options"] = {"is_disabled": True}
        return self.call("sendMessage", data)

    async def send_photo(self, chat_id, photo, caption="", **_):
        return self.call("sendPhoto", {"chat_id": chat_id, "caption": caption}, {"photo": ("image.png", photo)})

    async def send_document(self, chat_id, document, filename=None, caption=None, **_):
        p = Path(document)
        return self.call("sendDocument", {"chat_id": chat_id, "caption": caption},
                         {"document": (filename or p.name, p.read_bytes())})

    async def send_media_group(self, chat_id, media, **_):
        for m in media:  # 단순하게 하나씩
            self.call("sendDocument", {"chat_id": chat_id}, {"document": (m.media.filename, m.media.input_file_content)})


# ---------------------------------------------------------------- 작업

async def produce(tg: TG, tid: int) -> None:
    """주제 → 초안 → 바로 완성본 전송 (서버 모드는 왕복을 줄이려고 제목 선택 단계를 건너뜀)."""
    topic = store.get_topic(tid)
    await tg.send_message(bot.CHAT_ID, f"✍️ '{topic['keyword']}' 작성 중...")
    try:
        draft = await asyncio.to_thread(bot.build_draft, topic)
    except Exception as e:
        bot.log.exception("draft failed")
        store.set_topic_status(tid, "candidate")
        await tg.send_message(bot.CHAT_ID, f"⚠️ 초안 실패: {e}")
        return
    did = store.save_draft(tid, bot.today(), draft)
    if draft.get("_notice"):
        await tg.send_message(bot.CHAT_ID, draft["_notice"])
    titles = draft["titles"]
    # 한 계정 안에서 홈판용과 검색용 제목을 섞지 않는다 (config title_mode)
    if bot.CFG.get("title_mode", "homefeed") == "search":
        best = next((i for i, t in enumerate(titles) if "검색" in t.get("type", "")), 0)
    else:
        best = 0  # 홈판 모드: AI가 가장 클릭될 제목을 맨 앞에 둠
    others = "\n".join(f"· {t['text']}" for i, t in enumerate(titles) if i != best)
    if others:
        await tg.send_message(bot.CHAT_ID, f"🏷 다른 제목 후보 — 그대로 쓰지 말고 내 말투로 한 번 손보면 더 좋아요\n{others}")
    await bot.finalize(tg, did, best)


def slots() -> list[str]:
    return sorted(bot.CFG.get("post_slots") or bot.CFG.get("schedule_times", ["07:30"])[:1])


def due_slot(state: dict) -> str | None:
    """시각이 지났는데 오늘 아직 안 한 발행 슬롯 하나."""
    now = datetime.now(bot.TZ)
    if now.hour >= 19:  # 골든타임(07~19시) 밖에서는 새 글을 만들지 않음
        return None
    done = state.get("slots", {})
    done_today = done.get("done", []) if done.get("date") == bot.today() else []
    for s in slots():
        h, m = map(int, s.split(":"))
        if now.hour * 60 + now.minute >= h * 60 + m and s not in done_today:
            return s
    return None


def mark_slot(state: dict, slot: str) -> None:
    done = state.get("slots", {})
    if done.get("date") != bot.today():
        done = {"date": bot.today(), "done": []}
    done["done"].append(slot)
    state["slots"] = done


async def run_slot(tg: TG, state: dict, slot: str | None, forced: bool = False) -> None:
    """슬롯 하나 = 글 (하루 목표 / 슬롯 수)개. forced 는 /run."""
    if slot:
        mark_slot(state, slot)
    per = max(1, bot.CFG["posts_per_day"] // max(1, len(slots())))
    if bot.CFG.get("daily_mode", "candidates") == "remind":
        await tg.send_message(bot.CHAT_ID, "☀️ 글 쓸 시간이에요. 글감을 한 줄로 보내주세요. (10분 안에 처리돼요)")
        return
    await tg.send_message(bot.CHAT_ID, "🔎 지금 뜨는 글감 찾는 중..." + (f" ({slot} 슬롯)" if slot else ""))
    cands = await asyncio.to_thread(bot.build_candidates)
    if not cands:
        await tg.send_message(bot.CHAT_ID, "지금은 쓸 만한 후보가 없어요. 글감을 직접 보내주세요.")
        return
    rest = cands[per:]
    if rest:
        await tg.send_message(bot.CHAT_ID, "📋 다른 후보 (쓰고 싶으면 키워드를 그대로 보내세요)\n"
                              + "\n".join(f"· {t['keyword']} — {t.get('angle', '')}" for _, t in rest))
    for tid, _ in cands[:per]:
        store.claim_topic(tid)
        await produce(tg, tid)


async def daily(tg: TG, state: dict) -> None:
    """수동 실행(daily) — 지난 슬롯이 있으면 그걸, 없으면 한 편 강제로."""
    await run_slot(tg, state, due_slot(state), forced=True)


async def handle(tg: TG, upd: dict, state: dict) -> None:
    if "callback_query" in upd:
        q = upd["callback_query"]
        if q.get("message", {}).get("chat", {}).get("id") != bot.CHAT_ID:
            return
        try:
            tg.call("answerCallbackQuery", {"callback_query_id": q["id"]})
        except Exception:
            pass
        kind, *args = (q.get("data") or "").split(":")
        if kind == "pub":
            did = int(args[0])
            store.update_draft(did, status="published", published_at=datetime.now(bot.TZ).isoformat())
            d = store.get_draft(did)
            await tg.send_message(bot.CHAT_ID, f"👍 발행 기록 완료 · 오늘 {store.count_day(d['day'], ('published',))}/{bot.CFG['posts_per_day']}")
        elif kind == "pick" and store.claim_topic(int(args[0])):
            await produce(tg, int(args[0]))
        elif kind == "title":
            await bot.finalize(tg, int(args[0]), int(args[1]))
        return

    msg = upd.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()

    # 사진을 보내면 → 그 사진으로 홈판용 썸네일 만들기 (캡션 = 썸네일 문구)
    file_id = None
    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]
    elif (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
        file_id = msg["document"]["file_id"]
    if file_id and chat == bot.CHAT_ID:
        await make_thumb(tg, file_id, (msg.get("caption") or "").strip())
        return
    if not text:
        return
    if chat != bot.CHAT_ID:
        if text.startswith("/start"):
            await tg.send_message(chat, f"chat id: {chat}\nGitHub Secrets 의 TELEGRAM_CHAT_ID 에 넣어주세요.")
        return
    if text.startswith("/start"):
        await tg.send_message(chat, "✅ GitHub에서 작동 중이에요. (보낸 건 10분 안에 처리돼요)\n"
                              "· 글감 한 줄 → 정보형 완성본\n"
                              "· 후기: 장소명 + 줄바꿈 + 메모 → 내 경험 후기 글\n"
                              "· 사진 + 캡션(썸네일 문구) → 홈판용 썸네일\n"
                              "· 트렌드 + 줄바꿈 + 어드바이저 메인 트렌드 복사 → 주제·제목에 반영\n"
                              "· /run → 지금 한 편 바로 · /today → 현황")
    elif text.startswith("/run"):
        await run_slot(tg, state, None, forced=True)
    elif text.startswith("/today"):
        d = bot.today()
        await tg.send_message(chat, f"📊 {d} 완성 {store.count_day(d, ('approved', 'published'))} · 발행 {store.count_day(d, ('published',))} / 목표 {bot.CFG['posts_per_day']}")
    elif text.startswith("트렌드"):
        titles = parse_trends(text)
        if len(titles) < 3:
            await tg.send_message(chat, "크리에이터 어드바이저 → 트렌드 → 메인 유입 트렌드에서 글 목록을 통째로 복사해서\n\n트렌드\n(붙여넣기)\n\n이렇게 보내주세요.")
            return
        state["trends"] = {"date": bot.today(), "titles": titles}
        load_trends(state)
        await tg.send_message(chat, f"📈 홈판 트렌드 제목 {len(titles)}개 저장했어요. 3일 동안 주제 고르기와 제목 짓기에 반영돼요.\n지금 바로 반영해서 쓰려면 /run")
    elif text.startswith("후기"):
        body = text[2:].lstrip(":： \n")
        first, _, rest = body.partition("\n")
        if len(body) < 20:
            await tg.send_message(chat, "후기 메모가 너무 짧아요. 이렇게 보내주세요:\n\n후기: 한정선 신세계 강남점\n9시 20분 도착, 테이블링 73번째\n11시 입장 카톡 옴\n10개 57,700원\n맛있는데 웨이팅 생각하면 재방문은 글쎄")
            return
        await tg.send_message(chat, f"📝 '{first.strip()[:40]}' 후기로 쓸게요.")
        topic = {"keyword": first.strip()[:40], "entity": first.strip()[:20], "angle": "직접 다녀온 후기",
                 "why_now": "", "seed": bot.REVIEW_SEED,
                 "sources": [{"title": f"글쓴이 메모: {first.strip()}", "desc": "", "url": "", "naver_link": "",
                              "body": rest.strip() or body, "official": False}]}
        tid = store.add_topic(bot.today(), topic)
        store.claim_topic(tid)
        await produce(tg, tid)
    elif not text.startswith("/"):
        await tg.send_message(chat, f"📝 '{text[:60]}' 받았어요.")
        topic = await asyncio.to_thread(bot.manual_topic, text[:60])
        tid = store.add_topic(bot.today(), topic)
        store.claim_topic(tid)
        await produce(tg, tid)


async def make_thumb(tg: TG, file_id: str, caption: str) -> None:
    if not caption:  # 캡션이 없으면 오늘 마지막 초안의 썸네일 문구 사용
        with store.db() as c:
            row = c.execute("SELECT body FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
        caption = json.loads(row["body"]).get("thumbnail_text", "") if row else ""
    if not caption:
        await tg.send_message(bot.CHAT_ID, "사진에 캡션으로 썸네일 문구를 같이 보내주세요. 줄바꿈하면 마지막 줄이 노란 띠로 들어가요.\n예)\n2026\n사천에어쇼\n일정 및 주차장 정보")
        return
    f = tg.call("getFile", {"file_id": file_id})
    data = tg.http.get(f"https://api.telegram.org/file/bot{tg.token}/{f['file_path']}").content
    out = bot.OUT / "thumbs" / f"{file_id[-12:]}.jpg"
    path = cards.make_photo_thumb(data, caption, out, bot.font_path(), bot.CFG.get("font_index", 0))
    await tg.send_document(bot.CHAT_ID, path, filename="thumbnail.jpg",
                           caption="⭐ 썸네일 완성 (원본 화질 파일). 블로그 맨 위에 넣고 대표 이미지로 지정")


async def poll(tg: TG, state: dict) -> None:
    offset = state.get("offset")
    updates = tg.call("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]})
    for upd in updates:
        state["offset"] = upd["update_id"] + 1
        save_state(state)  # 처리 중 실패해도 같은 메시지를 무한 반복하지 않게 먼저 저장
        try:
            await handle(tg, upd, state)
        except Exception as e:
            bot.log.exception("update failed")
            try:
                await tg.send_message(bot.CHAT_ID, f"⚠️ 오류: {e}")
            except Exception:
                pass
    if updates:
        tg.call("getUpdates", {"offset": state["offset"], "timeout": 0})  # 처리 완료 확인


def load_trends(state: dict) -> None:
    """3일 이내에 받은 홈판 트렌드 제목을 봇에 넣는다."""
    tr = state.get("trends") or {}
    try:
        fresh = (datetime.now(bot.TZ).date() - datetime.fromisoformat(tr.get("date", "")).date()).days <= 3
    except ValueError:
        fresh = False
    bot.TREND_TITLES[:] = tr.get("titles", []) if fresh else []


def parse_trends(text: str) -> list[str]:
    """크리에이터 어드바이저에서 복사한 목록 → 제목만. 순위 숫자, 조회수, 날짜 줄은 버린다."""
    out = []
    for line in text.splitlines()[1:] if text.startswith("트렌드") else text.splitlines():
        t = re.sub(r"^\s*\d+\s*[.)위]?\s*", "", line).strip()
        if len(t) < 8 or re.fullmatch(r"[\d,.\s%:/-]+(회|명|분|시간 전|일 전)?", t):
            continue
        if t not in out:
            out.append(t)
    return out[:40]


async def main(mode: str) -> None:
    store.init()
    token = bot.os.environ.get(bot.CFG.get("telegram_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN secret 이 비어 있어요.")
    tg = TG(token)
    state = load_state()
    restored = bool(state)  # 이전 실행의 기억을 제대로 불러왔는지
    load_trends(state)
    try:
        await poll(tg, state)          # 쌓인 메시지 먼저
        if mode == "daily":
            await daily(tg, state)
        elif restored:
            slot = due_slot(state)
            if slot:  # 발행 슬롯 시각이 지났으면 이번 실행이 처리 (한 번에 하나씩)
                bot.log.info("slot %s due", slot)
                await run_slot(tg, state, slot)
    finally:
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "poll"))
