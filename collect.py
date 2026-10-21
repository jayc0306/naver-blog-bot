"""소재 수집: 네이버 데이터랩(검색어트렌드) + 뉴스 검색 API. 둘 다 네이버 공식 Open API."""
import html
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
TAG_RE = re.compile(r"<[^>]+>")


def _headers(cid: str, secret: str) -> dict:
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}


def clean(text: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or "")).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def growth(values: list[float]) -> float:
    """최근 3일 평균 / 그 전 2주 평균. 데이터랩 ratio는 요청 단위로 정규화되므로 같은 시리즈 안의 비율만 비교한다."""
    recent = statistics.mean(values[-3:])
    base = statistics.mean(values[-17:-3])
    return (recent + 1) / (base + 1)


def trend_scores(seeds: list[str], cid: str, secret: str, today: date | None = None) -> dict[str, float]:
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=29)
    days = [(start + timedelta(days=k)).isoformat() for k in range(30)]
    scores: dict[str, float] = {}
    with httpx.Client(timeout=20) as client:
        for i in range(0, len(seeds), 5):  # 데이터랩은 요청당 키워드 그룹 5개까지
            batch = seeds[i : i + 5]
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "timeUnit": "date",
                "keywordGroups": [{"groupName": s, "keywords": [s]} for s in batch],
            }
            r = client.post(DATALAB_URL, headers=_headers(cid, secret), json=body)
            r.raise_for_status()
            for res in r.json().get("results", []):
                series = {d["period"]: float(d["ratio"]) for d in res.get("data", [])}
                scores[res["title"]] = growth([series.get(d, 0.0) for d in days])
    return scores


def fetch_news(query: str, cid: str, secret: str, display: int = 10, max_age_hours: int = 48) -> list[dict]:
    r = httpx.get(
        NEWS_URL,
        headers=_headers(cid, secret),
        params={"query": query, "display": display, "sort": "date"},
        timeout=20,
    )
    r.raise_for_status()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    out = []
    for it in r.json().get("items", []):
        try:
            pub = parsedate_to_datetime(it["pubDate"])
        except (KeyError, TypeError, ValueError):
            continue
        if pub < cutoff:
            continue
        out.append(
            {
                "title": clean(it.get("title")),
                "desc": clean(it.get("description")),
                "url": it.get("originallink") or it.get("link"),
                "naver_link": it.get("link", ""),
                "pub": pub.isoformat(),
            }
        )
    return out


def fetch_article_text(url: str, limit: int = 3000) -> str:
    """네이버 뉴스에 호스팅된 기사만 본문을 가져온다 (구조가 일정해서). 나머지는 요약문만 사용."""
    if "news.naver.com" not in (url or ""):
        return ""
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.select_one("#dic_area") or soup.select_one("#newsct_article")
    return node.get_text("\n", strip=True)[:limit] if node else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def fetch_press_releases(service_key: str, endpoint: str, today: date, days: int = 2,
                         ministries: list[str] | None = None, max_items: int = 30,
                         body_limit: int = 4000) -> list[dict]:
    """정책브리핑 보도자료 API (공공데이터포털). serviceKey는 '디코딩' 키를 넣는다."""
    start = today - timedelta(days=max(days, 1) - 1)
    r = httpx.get(
        endpoint,
        params={"serviceKey": service_key, "startDate": start.strftime("%Y%m%d"), "endDate": today.strftime("%Y%m%d")},
        timeout=30,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)

    items = []
    for node in root.iter():
        if _local(node.tag) != "newsitem":
            continue
        f = {_local(c.tag): (c.text or "") for c in node}
        if f.get("groupingcode") and f["groupingcode"].lower() != "brief":
            continue
        ministry = f.get("ministercode", "").strip()
        if ministries and not any(m in ministry for m in ministries):
            continue
        body = clean(f.get("datacontents", ""))
        sub = clean(f.get("subtitle1", ""))
        items.append(
            {
                "title": clean(f.get("title", "")),
                "desc": sub or body[:200],
                "url": f.get("originalurl", "").strip(),
                "naver_link": "",
                "body": body[:body_limit],
                "ministry": ministry,
                "pub": f.get("approvedate", "").strip(),
                "official": True,
            }
        )

    if not items:  # 인증 오류 등은 정상 응답 형태로 오므로 메시지를 꺼내서 알린다
        msgs = {_local(n.tag): (n.text or "").strip() for n in root.iter()}
        code = msgs.get("resultcode") or msgs.get("returnreasoncode")
        if code and code not in ("0", "00", "03"):  # 03 = 데이터 없음
            raise RuntimeError(f"보도자료 API 오류 {code}: {msgs.get('resultmsg') or msgs.get('returnauthmsg')}")
    items.sort(key=lambda x: x["pub"], reverse=True)
    return items[:max_items]


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def fetch_news_google(query: str, display: int = 10, max_age_hours: int = 48) -> list[dict]:
    """네이버 키가 없을 때 쓰는 대체 뉴스 수집 (구글 뉴스 RSS, 키 불필요). 본문 없이 제목·언론사만."""
    r = httpx.get(
        GOOGLE_NEWS_RSS,
        params={"q": f"{query} when:{max(1, max_age_hours // 24)}d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
        headers={"User-Agent": UA},
        timeout=20,
        follow_redirects=True,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    out = []
    for it in root.iter("item"):
        title = clean(it.findtext("title", ""))
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate", ""))
        except (TypeError, ValueError):
            continue
        if pub < cutoff:
            continue
        source = clean(it.findtext("source", ""))
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append(
            {
                "title": title,
                "desc": f"({source})" if source else "",
                "url": it.findtext("link", ""),
                "naver_link": "",
                "pub": pub.isoformat(),
            }
        )
        if len(out) >= display:
            break
    return out


def fetch_festivals(service_key: str, endpoint: str, today: date, days_ahead: int = 14,
                    max_items: int = 40) -> list[dict]:
    """한국관광공사 TourAPI 행사정보 (공공데이터포털, DATA_GO_KR_KEY 공용). 오늘 진행 중이거나 days_ahead 안에 시작하는 행사."""
    from urllib.parse import quote
    r = httpx.get(
        endpoint,
        params={"serviceKey": service_key, "MobileOS": "ETC", "MobileApp": "blogbot", "_type": "json",
                "numOfRows": 300, "pageNo": 1, "arrange": "A",
                "eventStartDate": (today - timedelta(days=45)).strftime("%Y%m%d")},
        timeout=30,
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"관광공사 API 응답 오류: {r.text[:150]}")
    body = (data.get("response") or {}).get("body") or {}
    items = (body.get("items") or {}) if isinstance(body.get("items"), dict) else {}
    rows = items.get("item") or []
    if isinstance(rows, dict):
        rows = [rows]
    lo, hi = today.strftime("%Y%m%d"), (today + timedelta(days=days_ahead)).strftime("%Y%m%d")
    out = []
    for it in rows:
        s, e = str(it.get("eventstartdate", "")), str(it.get("eventenddate", ""))
        if not s or not e or e < lo or s > hi:
            continue
        title = clean(it.get("title", ""))
        fmt = lambda x: f"{x[4:6]}.{x[6:8]}" if len(x) == 8 else x
        out.append({
            "title": title,
            "desc": f"{fmt(s)}~{fmt(e)} · {clean(it.get('addr1', ''))}",
            "url": "https://korean.visitkorea.or.kr/search/search_list.do?keyword=" + quote(title),
            "naver_link": "",
            "body": f"행사명: {title}\n기간: {s}~{e}\n주소: {clean(it.get('addr1', ''))} {clean(it.get('addr2', ''))}\n문의: {clean(it.get('tel', ''))}",
            "ministry": "한국관광공사",
            "kind": "관광정보",
            "pub": s,
            "official": True,
        })
    out.sort(key=lambda x: x["pub"])
    return out[:max_items]
