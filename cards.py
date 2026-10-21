"""텍스트 카드 이미지 생성 (1080x1080). 첫 장은 썸네일."""
from pathlib import Path

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

FALLBACK_FONTS = [
    str(Path(__file__).parent / "fonts" / "Pretendard-ExtraBold.otf"),
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
]
SIZE = 1080
PAD = 100


def _font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    for p, i in [(path, index)] + [(f, 1 if "Noto" in f else 0) for f in FALLBACK_FONTS]:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size, index=i)
            except OSError:
                try:
                    return ImageFont.truetype(p, size, index=0)
                except OSError:
                    continue
    raise FileNotFoundError("한글 폰트를 찾지 못함. config.yaml의 font_path를 지정하세요.")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:  # 한 단어가 너무 길면 글자 단위로
                if draw.textlength(cur + ch, font=font) > width and cur:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur)
    return [l for l in lines if l]


def _fit(draw, text, font_path, font_index, max_size, min_size, width, max_height):
    size = max_size
    while size >= min_size:
        font = _font(font_path, font_index, size)
        lines = _wrap(draw, text, font, width)
        h = len(lines) * int(size * 1.3)
        if h <= max_height:
            return font, lines, size
        size -= 6
    font = _font(font_path, font_index, min_size)
    return font, _wrap(draw, text, font, width), min_size


def make_card(text: str, out: Path, font_path: str, font_index: int, colors: list[str],
              label: str = "", thumbnail: bool = False) -> Path:
    bg, accent = colors
    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)
    width = SIZE - PAD * 2

    if thumbnail:
        d.rectangle([0, SIZE - 60, SIZE, SIZE], fill=accent)
    else:
        d.rectangle([PAD, PAD, PAD + 120, PAD + 16], fill=accent)

    font, lines, size = _fit(d, text, font_path, font_index,
                             150 if thumbnail else 140, 56, width, SIZE - PAD * 3)
    lh = int(size * 1.3)
    y = (SIZE - lh * len(lines)) // 2
    for i, line in enumerate(lines):
        color = accent if thumbnail and i == len(lines) - 1 else "#FFFFFF"
        w = d.textlength(line, font=font)
        d.text(((SIZE - w) / 2, y), line, font=font, fill=color)
        y += lh

    if label:
        lf = _font(font_path, font_index, 34)
        d.text((PAD, SIZE - PAD - 20), label, font=lf, fill="#888888")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


def make_table_card(text: str, out: Path, font_path: str, font_index: int, colors: list[str]) -> Path:
    """'항목|내용' 줄들을 요약표 이미지로 (1080 x 가변 높이)."""
    bg, accent = colors
    rows = [tuple(x.strip() for x in line.split("|", 1)) for line in text.split("\n") if "|" in line]
    rows = rows[:9] or [("내용", text[:40])]
    W, pad, key_w = 1080, 70, 300
    tmp = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    kf, vf = _font(font_path, font_index, 44), _font(font_path, font_index, 44)
    laid, h = [], pad
    for k, v in rows:
        vl = _wrap(tmp, v, vf, W - pad * 2 - key_w - 30) or [""]
        rh = max(1, len(vl)) * 60 + 40
        laid.append((k, vl, rh))
        h += rh
    h += pad
    img = Image.new("RGB", (W, h), bg)
    d = ImageDraw.Draw(img)
    y = pad
    for i, (k, vl, rh) in enumerate(laid):
        if i:
            d.line([pad, y, W - pad, y], fill="#3A4A66", width=2)
        d.text((pad, y + 20), k, font=kf, fill=accent)
        for j, line in enumerate(vl):
            d.text((pad + key_w + 30, y + 20 + j * 60), line, font=vf, fill="#FFFFFF")
        y += rh
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


IMAGE_BLOCKS = ("card", "table")  # 이미지로 렌더링되는 블록 (번호를 같이 매김)


def make_cards(draft: dict, folder: Path, prefix: str, font_path: str, font_index: int,
               colors: list[str], label: str = "") -> list[Path]:
    paths = [make_card(draft.get("thumbnail_text", ""), folder / f"{prefix}_00_thumb.png",
                       font_path, font_index, colors, thumbnail=True)]
    n = 0
    for b in draft["blocks"]:
        t = b.get("type")
        if t not in IMAGE_BLOCKS:
            continue
        n += 1
        out = folder / f"{prefix}_{n:02d}.png"
        if t == "table":
            paths.append(make_table_card(b["content"], out, font_path, font_index, colors))
        else:
            paths.append(make_card(b["content"], out, font_path, font_index, colors, label=label))
    return paths


def make_photo_thumb(photo: bytes, text: str, out: Path, font_path: str, font_index: int,
                     band: str = "#FFD84D") -> Path:
    """내 사진 + 큰 제목 썸네일 (1080x1080). 마지막 줄은 노란 띠 위에 검은 글씨.
    홈판 썸네일처럼: 위쪽 어둡게, 큰 흰 글씨에 검은 테두리."""
    img = Image.open(BytesIO(photo)).convert("RGB")
    img = ImageOps.fit(img, (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.55))
    # 위쪽 60%를 아래로 갈수록 옅어지는 검은 그라데이션으로 덮기
    shade = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        shade.putpixel((0, y), max(0, int(190 * (1 - y / (SIZE * 0.62)))))
    img = Image.composite(Image.new("RGB", (SIZE, SIZE), "black"), img, shade.resize((SIZE, SIZE)))
    d = ImageDraw.Draw(img)

    lines = [l.strip() for l in text.replace("/", "\n").split("\n") if l.strip()][:3] or [" "]
    head, tail = (lines[:-1], lines[-1]) if len(lines) >= 2 else (lines, None)
    y = 90
    for line in head:
        size = 170
        while size > 70:
            f = _font(font_path, font_index, size)
            if d.textlength(line, font=f) <= SIZE - 120:
                break
            size -= 8
        w = d.textlength(line, font=f)
        d.text(((SIZE - w) / 2, y), line, font=f, fill="#FFFFFF", stroke_width=max(6, size // 18), stroke_fill="#111111")
        y += int(size * 1.18)
    if tail:
        size = 92
        while size > 48:
            f = _font(font_path, font_index, size)
            if d.textlength(tail, font=f) <= SIZE - 220:
                break
            size -= 6
        w = d.textlength(tail, font=f)
        pad_x, pad_y = 40, 22
        x0 = (SIZE - w) / 2 - pad_x
        d.rectangle([x0, y + 10, x0 + w + pad_x * 2, y + 10 + size + pad_y * 2], fill=band)
        d.text(((SIZE - w) / 2, y + 10 + pad_y - size * 0.08), tail, font=f, fill="#111111")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out
