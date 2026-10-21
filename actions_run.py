"""GitHub Actions용 실행기. 상시 켜진 봇 대신, 정해진 시간마다 잠깐 켜져서 할 일을 처리하고 꺼진다.

  python actions_run.py daily   매일 아침: 주제를 알아서 골라 글 N개를 완성본으로 보냄
  python actions_run.py poll    30분마다: 그동안 텔레그램에 보낸 글감·버튼을 처리
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

import bot  # 설정·파이프라인 재사용 (텔레그램 상시 연결은 안 씀)
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
    best = next((i for i, t in enumerate(titles) if "검색" in t.get("type", "")), 0)
    others = "\n".join(f"· {t['text']}" for i, t in enumerate(titles) if i != best)
    if others:
        await tg.send_message(bot.CHAT_ID, f"🏷 다른 제목 후보 (마음에 들면 제목만 바꿔 쓰세요)\n{others}")
    await bot.finalize(tg, did, best)


async def daily(tg: TG, state: dict) -> None:
    today = bot.today()
    if state.get("last_daily") == today:
        bot.log.info("daily already done today")
        return
    state["last_daily"] = today
    if bot.CFG.get("daily_mode", "candidates") == "remind":
        await tg.send_message(bot.CHAT_ID, "☀️ 오늘 글감을 한 줄로 보내주세요. (30분 안에 처리돼요)")
        return
    await tg.send_message(bot.CHAT_ID, "☀️ 오늘 글감 찾는 중...")
    cands = await asyncio.to_thread(bot.build_candidates)
    if not cands:
        await tg.send_message(bot.CHAT_ID, "오늘은 쓸 만한 후보가 없어요. 글감을 직접 보내주세요.")
        return
    n = bot.CFG["posts_per_day"]
    rest = cands[n:]
    if rest:
        await tg.send_message(bot.CHAT_ID, "📋 다른 후보 (쓰고 싶으면 키워드를 그대로 보내세요)\n"
                              + "\n".join(f"· {t['keyword']} — {t.get('angle', '')}" for _, t in rest))
    for tid, _ in cands[:n]:
        store.claim_topic(tid)
        await produce(tg, tid)


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
    if not text:
        return
    if chat != bot.CHAT_ID:
        if text.startswith("/start"):
            await tg.send_message(chat, f"chat id: {chat}\nGitHub Secrets 의 TELEGRAM_CHAT_ID 에 넣어주세요.")
        return
    if text.startswith("/start"):
        await tg.send_message(chat, "✅ GitHub에서 작동 중이에요.\n· 글감을 한 줄로 보내면 30분 안에 완성본이 와요\n· /run → 지금 바로 오늘 글 찾기\n· 매일 아침 7시 반쯤 자동으로 와요")
    elif text.startswith("/run"):
        state.pop("last_daily", None)
        await daily(tg, state)
    elif text.startswith("/today"):
        d = bot.today()
        await tg.send_message(chat, f"📊 {d} 완성 {store.count_day(d, ('approved', 'published'))} · 발행 {store.count_day(d, ('published',))} / 목표 {bot.CFG['posts_per_day']}")
    elif not text.startswith("/"):
        await tg.send_message(chat, f"📝 '{text[:60]}' 받았어요.")
        topic = await asyncio.to_thread(bot.manual_topic, text[:60])
        tid = store.add_topic(bot.today(), topic)
        store.claim_topic(tid)
        await produce(tg, tid)


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


async def main(mode: str) -> None:
    store.init()
    token = bot.os.environ.get(bot.CFG.get("telegram_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN secret 이 비어 있어요.")
    tg = TG(token)
    state = load_state()
    try:
        await poll(tg, state)          # 쌓인 메시지 먼저
        if mode == "daily":
            await daily(tg, state)
    finally:
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "poll"))
