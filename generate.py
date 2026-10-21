"""Claude로 주제 제안 + 초안 생성 + 붙여넣기용 텍스트 렌더링."""
import json
import logging
import re

import anthropic

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("응답에서 JSON을 찾지 못함")
    return json.loads(text[start : end + 1])


def call_json(model: str, system: str, user: str, schema: dict, tool_name: str = "submit",
              max_tokens: int = 8000, retries: int = 2, extra_tools: list | None = None) -> dict:
    """도구로 제출하게 해서 JSON 객체로 받는다. 웹검색 같은 서버 도구가 길어지면(pause_turn) 이어서 진행."""
    tool = {"name": tool_name, "description": "결과를 이 도구로 제출한다. 반드시 호출할 것.", "input_schema": schema}
    sys_prompt = system + f"\n\n최종 결과는 설명 없이 반드시 {tool_name} 도구를 호출해서 제출한다."
    tools = (extra_tools or []) + [tool]
    last = ""
    for attempt in range(retries + 1):
        messages = [{"role": "user", "content": user}]
        msg = None
        for _ in range(5):  # pause_turn 이어가기
            msg = client().messages.create(
                model=model, max_tokens=max_tokens, system=sys_prompt,
                messages=messages, tools=tools, tool_choice={"type": "auto"},
            )
            if msg.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": msg.content}]
        for b in msg.content:
            if getattr(b, "type", "") == "tool_use" and getattr(b, "name", tool_name) == tool_name \
                    and isinstance(b.input, dict) and b.input:
                return b.input
        text = "".join(getattr(b, "text", "") or "" for b in msg.content if getattr(b, "type", "") == "text")
        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError):
            last = f"stop={msg.stop_reason}, text={text[:120]!r}"
            logging.getLogger("blogbot").warning("AI 형식 오류 (시도 %d): %s", attempt + 1, last)
    raise RuntimeError(f"AI 응답 형식 오류 ({last})")


# ---------------------------------------------------------------- 주제 제안

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "entity": {"type": "string"},
                    "angle": {"type": "string"},
                    "why_now": {"type": "string"},
                    "seed": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["keyword", "entity", "angle", "why_now", "source_ids"],
            },
        }
    },
    "required": ["topics"],
}

DRAFT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "items": {"type": "object", "properties": {"type": {"type": "string"}, "text": {"type": "string"}},
                      "required": ["type", "text"]},
        },
        "thumbnail_text": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["text", "heading", "card", "table", "photo", "embed", "me", "aiimg", "place", "link"]},
                    "content": {"type": "string"},
                },
                "required": ["type", "content"],
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "fact_checks": {
            "type": "array",
            "items": {"type": "object", "properties": {"claim": {"type": "string"}, "source": {"type": "string"}},
                      "required": ["claim", "source"]},
        },
    },
    "required": ["titles", "thumbnail_text", "blocks", "tags", "fact_checks"],
}


TOPIC_SYSTEM = """너는 네이버 블로그 편집자다. 오늘 뉴스 헤드라인을 보고 블로그 글감 후보를 고른다.
공통 기준:
- 대중이 지금 궁금해할 것. 독자가 바로 써먹을 정보가 있는 것.
- keyword는 실제로 검색할 법한 2~5단어 롱테일 검색어. 대형 키워드 단독 금지.
- 같은 이슈를 두 번 고르지 말 것. 정치 논쟁, 의료 효능, 투자 종목 추천은 제외.
- 반드시 주어진 헤드라인으로 뒷받침되는 주제만. source_ids에 근거 헤드라인 id를 넣어라.
"""


def propose_topics(model: str, category: str, news_by_seed: dict[str, list[dict]], n: int,
                   used: list[str], blocklist: list[str], style_notes: str, topic_rules: str = "") -> list[dict]:
    lines, flat = [], {}
    for si, (seed, items) in enumerate(news_by_seed.items()):
        lines.append(f"\n## 시드: {seed}")
        for ni, it in enumerate(items):
            sid = f"{si}-{ni}"
            flat[sid] = it
            lines.append(f"[{sid}] {it['title']} — {it['desc']}")

    user = f"""카테고리: {category}
카테고리 규칙:
{topic_rules}
최근 30일 내 이미 쓴 키워드(피할 것): {', '.join(used) or '없음'}

오늘 헤드라인:
{chr(10).join(lines)}

참고 스타일 노트:
{style_notes}

후보 {n}개를 아래 구조로 제출:
{{"topics":[{{"keyword":"...","entity":"핵심 고유명사","angle":"이 글만의 각도 한 줄","why_now":"지금 써야 하는 이유 한 줄","seed":"시드명","source_ids":["0-1","0-3"]}}]}}"""

    data = call_json(model, TOPIC_SYSTEM, user, TOPIC_SCHEMA, "submit_topics", max_tokens=8000)
    used_set = {u.replace(" ", "") for u in used}
    out = []
    for t in data.get("topics", []):
        kw = (t.get("keyword") or "").strip()
        if not kw or kw.replace(" ", "") in used_set:
            continue
        if any(b in kw or b in t.get("angle", "") for b in blocklist):
            continue
        t["entity"] = (t.get("entity") or kw.split()[0]).strip()
        srcs = [flat[s] for s in t.get("source_ids", []) if s in flat]
        if not srcs:
            continue
        t["keyword"], t["sources"] = kw, srcs
        out.append(t)
        used_set.add(kw.replace(" ", ""))
    return out[:n]


# ---------------------------------------------------------------- 초안

DRAFT_SYSTEM = """너는 네이버 블로그 글 초안을 쓴다. 사람이 검수하고 자기 생각을 덧붙여 발행한다.
절대 규칙:
1. 사실(날짜, 수치, 이름, 조건)은 제공된 출처나 web_search로 직접 확인한 것만 쓴다.
   확인 못 한 세부사항은 [확인 필요]로 남기지 말고 아예 언급하지 않는다. 확인된 내용만으로 글을 구성한다.
   단, 독자에게 꼭 필요한 핵심(신청 기간, 대상, 금액)을 끝내 확인 못 했을 때만 [확인 필요]를 쓰고, 글 전체에서 최대 2개.
   web_search를 쓸 수 있으면, 출처가 제목뿐이거나 핵심 정보가 빠졌을 때 정부·공공기관 공식 페이지(gov.kr, korea.kr, 각 부처, 국세청, 복지로 등)를 우선 찾아 확인한다.
2. 출처 문장을 그대로 옮기지 않는다. 내용을 이해하고 완전히 새 문장으로 쓴다.
3. 본문에 사실을 쓸 때마다 fact_checks에 그 주장과 실제로 확인한 페이지 URL을 넣는다. 날짜·수치·이름은 빠짐없이.
4. "me" 블록을 정확히 1개 넣는다. 글쓴이가 한두 줄 덧붙일 자리다. content에는 답하기 쉬운 구체적 질문 하나만 적는다.
5. 과장, 허위, 투자 권유, 의료 효능 주장 금지.
6. 제목 5개, 유형별 하나씩: 숫자형, 손실회피형, 비교형, 궁금증형, 검색형. 모두 메인 키워드 포함, 가능하면 앞쪽. 제목이 던진 질문은 본문이 답해야 한다.
"""

DRAFT_SCHEMA = """{
 "titles":[{"type":"숫자형","text":"..."},{"type":"손실회피형","text":"..."},{"type":"비교형","text":"..."},{"type":"궁금증형","text":"..."},{"type":"검색형","text":"..."}],
 "thumbnail_text":"대표 이미지 문구. 2줄, 줄당 10자 내외, \\n으로 구분",
 "blocks":[
   {"type":"text","content":"도입. 결론 먼저. 1~2문장마다 \\n\\n 줄바꿈"},
   {"type":"card","content":"..."},
   {"type":"heading","content":"소제목"},
   {"type":"text","content":"..."},
   {"type":"me","content":"..."},
   {"type":"embed","content":"..."}
 ],
 "tags":["중요한 순서대로 15~25개, # 없이, 띄어쓰기 없이"],
 "fact_checks":[{"claim":"...","source":"https://..."}]
}"""


def format_sources(articles: list[dict]) -> str:
    parts = []
    for i, a in enumerate(articles, 1):
        body = a.get("body") or a.get("desc", "")
        kind = f"{a.get('kind', '정부 보도자료')} · {a.get('ministry', '')}" if a.get("official") else "뉴스"
        parts.append(f"[출처 {i}] ({kind}) {a['title']}\nURL: {a['url']}\n{body}")
    return "\n\n".join(parts)


WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3,
                   "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}


def write_draft(model: str, category: str, topic: dict, articles: list[dict], style_notes: str,
                draft_rules: str = "", media_rule: str = "", web_search: bool = True, me_max: int = 1) -> dict:
    user = f"""카테고리: {category}
카테고리 규칙:
{draft_rules}

이미지 규칙:
{media_rule}

메인 키워드: {topic['keyword']}
각도: {topic.get('angle', '')}
지금 쓰는 이유: {topic.get('why_now', '')}

스타일 노트:
{style_notes}

출처:
{format_sources(articles)}

아래 구조로 작성해서 제출:
{DRAFT_SCHEMA}"""
    notice = ""
    if web_search:
        try:
            draft = call_json(model, DRAFT_SYSTEM, user, DRAFT_TOOL_SCHEMA, "submit_draft",
                              max_tokens=16000, extra_tools=[WEB_SEARCH_TOOL])
        except anthropic.APIStatusError as e:
            if e.status_code not in (400, 403):
                raise
            logging.getLogger("blogbot").warning("web search unavailable: %s", e)
            notice = ("ℹ️ 웹 검색이 꺼져 있어서 뉴스 제목만으로 썼어요. "
                      "console.anthropic.com → Settings → Privacy 에서 Web search 를 켜면 [확인 필요]가 줄어요.")
            draft = call_json(model, DRAFT_SYSTEM, user, DRAFT_TOOL_SCHEMA, "submit_draft", max_tokens=16000)
    else:
        draft = call_json(model, DRAFT_SYSTEM, user, DRAFT_TOOL_SCHEMA, "submit_draft", max_tokens=16000)
    if notice:
        draft["_notice"] = notice
    if not draft.get("titles") or not draft.get("blocks"):
        raise RuntimeError("초안 형식이 올바르지 않음")
    return sanitize(draft, me_max)


# ---------------------------------------------------------------- 정리 (코드로 강제)

UNSURE = re.compile(r"\[?\s*확인\s*필요\s*\]?")


def sanitize(draft: dict, me_max: int = 1) -> dict:
    """[확인 필요]가 들어간 문장·카드는 본문에서 빼서 따로 모으고, 직접 쓰기 칸은 me_max개만 남긴다."""
    removed: list[str] = []
    blocks, me_count = [], 0
    for b in draft.get("blocks", []):
        t, c = b.get("type"), b.get("content") or ""
        if t == "me":
            me_count += 1
            if me_count <= me_max:
                blocks.append(b)
            continue
        if t in ("card", "heading") and UNSURE.search(c):
            removed.append(UNSURE.sub("", c).strip())
            continue
        if t == "text" and UNSURE.search(c):
            keep = []
            # 문장 단위로 잘라서 확인 필요 문장만 뺀다
            for line in c.split("\n"):
                parts = re.split(r"(?<=[.!?다요])\s+", line)
                good = [p for p in parts if not UNSURE.search(p)]
                removed += [UNSURE.sub("", p).strip() for p in parts if UNSURE.search(p)]
                keep.append(" ".join(good))
            c = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
            if not c:
                continue
            b = {**b, "content": c}
        blocks.append(b)
    # 남은 블록이 소제목으로 끝나거나 소제목이 연속이면 정리
    cleaned = []
    for i, b in enumerate(blocks):
        nxt = blocks[i + 1]["type"] if i + 1 < len(blocks) else None
        if b["type"] == "heading" and nxt in (None, "heading"):
            continue
        cleaned.append(b)
    draft["blocks"] = cleaned
    draft["titles"] = [x for x in draft.get("titles", []) if not UNSURE.search(x.get("text", ""))] or draft.get("titles", [])
    if UNSURE.search(draft.get("thumbnail_text", "")):
        draft["thumbnail_text"] = UNSURE.sub("", draft["thumbnail_text"]).strip()
        if len(draft["thumbnail_text"].replace("\n", "")) < 6 and draft["titles"]:
            draft["thumbnail_text"] = draft["titles"][0]["text"][:24]
    draft["fact_checks"] = [f for f in draft.get("fact_checks", []) if not UNSURE.search(f.get("claim", ""))]
    draft["_removed"] = [r for r in removed if r]
    return draft


# ---------------------------------------------------------------- 렌더링

TAG_MAX_COUNT = 30   # 네이버 블로그 태그: 최대 30개
TAG_MAX_LEN = 100    # 태그 하나당 띄어쓰기 포함 100자


def tags_line(tags: list[str]) -> str:
    """태그 칸에 그대로 붙여넣는 형식: 쉼표로 구분, 최대 30개, 중복 제거."""
    out, seen = [], set()
    for t in tags:
        t = str(t).strip().lstrip("#").replace(" ", "")[:TAG_MAX_LEN]
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= TAG_MAX_COUNT:
            break
    return ", ".join(out)

def render(draft: dict, title_idx: int, footer: str = "") -> str:
    """스마트에디터에 그대로 붙여넣는 텍스트."""
    lines = [draft["titles"][title_idx]["text"], "", "🖼 [대표 이미지: 카드 00 썸네일]", ""]
    card_no = 0
    for b in draft["blocks"]:
        t, c = b.get("type"), (b.get("content") or "").strip()
        if t == "heading":
            lines += ["", f"■ {c}", ""]
        elif t == "text":
            lines += [c, ""]
        elif t == "photo":
            lines += [f"📷 [사진] {c}", ""]
        elif t in ("card", "table"):
            card_no += 1
            lines += [f"🖼 [카드 {card_no:02d}] {c.replace(chr(10), ' / ')[:60]}", ""]
        elif t == "aiimg":
            lines += [f"🎨 [AI 이미지] {c}", ""]
        elif t == "place":
            lines += [f"📍 [지도 첨부] {c}", ""]
        elif t == "link":
            label, _, url = c.partition("|")
            lines += [f"🔗 {label.strip()}", url.strip() or label.strip(), ""]
        elif t == "embed":
            lines += [f"▶️ [영상 임베드] {c}", ""]
        elif t == "me":
            lines += [f"✍️ [직접 쓰기] {c}", ""]
    if footer:
        lines += ["", footer]
    lines += ["", "태그: " + tags_line(draft.get("tags", []))]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def render_checklist(draft: dict) -> str:
    out = [f"🖼 썸네일 문구: {draft.get('thumbnail_text', '')}", "", "🔎 팩트체크"]
    for i, f in enumerate(draft.get("fact_checks", []), 1):
        out.append(f"{i}. {f.get('claim', '')}\n   {f.get('source', '')}")
    if draft.get("_removed"):
        out += ["", "🗑 확인이 안 돼서 본문에서 뺀 내용 (넣고 싶으면 직접 확인 후 추가)"]
        out += [f"· {r[:80]}" for r in draft["_removed"][:6]]
    types_ = {b.get("type") for b in draft.get("blocks", [])}
    n_me = sum(1 for b in draft.get("blocks", []) if b.get("type") == "me")
    out += ["", "✅ 발행 전 체크"]
    if n_me:
        out.append(f"· ✍️ 직접 쓰기 {n_me}곳 채우기")
    if "card" in types_:
        out.append("· 🖼 카드 이미지 순서대로 넣기 (00이 대표 이미지)")
    if "photo" in types_:
        out.append("· 📷 사진 자리는 직접 찍거나 캡처한 것만")
    if "embed" in types_:
        out.append("· ▶️ 공식 채널 영상 임베드")
    if "aiimg" in types_:
        out.append("· 🎨 AI 이미지는 에디터에서 'AI 활용' 표시 켜기")
    if "place" in types_:
        out.append("· 📍 에디터 '장소' 버튼으로 지도 첨부")
    if "link" in types_:
        out.append("· 🔗 링크는 열어서 맞는 페이지인지 확인")
    out += [
        "· 뉴스 사진·남의 이미지 쓰지 않기",
        "· 모바일 미리보기로 줄바꿈 확인",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------- 폰 발행용 조각

def segments(draft: dict, title_idx: int, footer: str = "") -> list[tuple]:
    """블로그 앱에 붙여넣는 순서대로 조각낸다.
    ("text", 문자열) / ("card", 번호, 문구) / ("photo", 설명) / ("embed", 설명) / ("tags", 문자열)"""
    out: list[tuple] = []
    buf: list[str] = []

    def flush():
        txt = re.sub(r"\n{3,}", "\n\n", "\n".join(buf)).strip()
        if txt:
            out.append(("text", txt))
        buf.clear()

    card_no = 0
    for b in draft["blocks"]:
        t, c = b.get("type"), (b.get("content") or "").strip()
        if t == "heading":
            buf += ["", f"■ {c}", ""]
        elif t == "text":
            buf += [c, ""]
        elif t == "me":
            buf += [f"✍️ [직접 쓰기] {c}", ""]
        elif t in ("card", "table"):
            flush()
            card_no += 1
            out.append(("card", card_no, c))
        elif t == "aiimg":
            flush()
            out.append(("aiimg", c))
        elif t == "place":
            flush()
            out.append(("place", c))
        elif t == "link":
            label, _, url = c.partition("|")
            url = (url or label).strip()
            if url.startswith("http"):
                flush()
                out.append(("link", label.strip() if _ else "공식 안내", url))
        elif t == "photo":
            flush()
            out.append(("photo", c))
        elif t == "embed":
            flush()
            out.append(("embed", c))
    if footer:
        buf += ["", footer]
    flush()
    tags = tags_line(draft.get("tags", []))
    if tags:
        out.append(("tags", tags))
    return out
