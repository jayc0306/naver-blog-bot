"""Windows/local browser bridge: existing SQLite draft -> Naver temporary draft.

No password collection, publishing, scheduling, or private Naver API calls.
Live editor selectors require verification in the owner's logged-in browser.
"""
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import yaml

import cards
import generate

BASE = Path(__file__).resolve().parent
STATE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'NaverBlogBot'
TITLE = '.se-title-text [contenteditable="true"]'
BODY = '.se-section-text .se-text-paragraph[contenteditable="true"]'
IMAGES = '.se-component.se-image'
SAVE_NAME = re.compile(r'^(?:임시\s*저장|저장)(?:\s*\d+)?$')
SUCCESS = re.compile(r'(?:임시\s*)?저장(?:이)?\s*(?:되었|완료)')


class DraftError(RuntimeError):
    pass


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def read_draft(database, draft_id):
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT body,title_idx FROM drafts WHERE id=?', (draft_id,)).fetchone()
    if row is None:
        raise DraftError('해당 초안 번호가 DB에 없습니다.')
    return json.loads(row[0]), row[1] if row[1] is not None else 0


def export_bundle(draft, title_index, config, target):
    title = draft['titles'][title_index]['text'].strip()
    if not title or not draft.get('blocks'):
        raise DraftError('제목과 본문이 있는 초안이 필요합니다.')
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=False)
    font = Path(config.get('font_path', 'fonts/Pretendard-ExtraBold.otf'))
    if not font.is_absolute():
        font = BASE / font
    images = cards.make_cards(draft, target / 'images', 'draft', str(font),
                              config.get('font_index', 0), config['card_colors'], config.get('card_label', ''))
    blocks = [{'type': 'image', 'file': str(images[0].relative_to(target)).replace('\\', '/')}]
    for seg in generate.segments(draft, title_index):
        kind = seg[0]
        if kind == 'tags':
            continue  # Tags are selected by the owner in the publish UI.
        if kind == 'card':
            blocks.append({'type': 'image', 'file': str(images[seg[1]].relative_to(target)).replace('\\', '/')})
        else:
            labels = {'photo': '[사진 추가]', 'aiimg': '[AI 이미지 제작]', 'place': '[지도 추가]',
                      'embed': '[영상 추가]', 'link': '[공식 링크]'}
            text = seg[1] if kind == 'text' else labels.get(kind, '[직접 확인]') + ' ' + '\n'.join(map(str, seg[1:]))
            blocks.append({'type': 'text', 'text': text})
    packet = {'schema_version': 1, 'title': title, 'blocks': blocks,
              'tags': draft.get('tags', []), 'checklist': generate.render_checklist(draft)}
    (target / 'manifest.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    body = []
    for block in blocks:
        if block['type'] == 'image':
            body.append('<img src="' + html.escape(block['file'], quote=True) + '" alt="카드">')
        else:
            body.append('<p>' + html.escape(block['text']).replace('\n', '<br>') + '</p>')
    preview = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>초안 검토</title>'
               '<style>body{max-width:700px;margin:40px auto;padding:20px;font:18px/1.8 sans-serif}'
               'img{max-width:100%}pre{white-space:pre-wrap}</style><h1>' + html.escape(title) + '</h1>'
               + ''.join(body) + '<hr><h2>발행 전 검토</h2><pre>' + html.escape(packet['checklist'])
               + '</pre><p>태그 후보: ' + html.escape(generate.tags_line(packet['tags'])) + '</p></html>')
    (target / 'preview.html').write_text(preview, encoding='utf-8')
    return target / 'manifest.json'


def load_bundle(manifest):
    manifest = Path(manifest).resolve(strict=True)
    packet = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if packet.get('schema_version') != 1 or not isinstance(packet.get('title'), str) or not packet['title'].strip():
        raise DraftError('지원하지 않는 초안 형식입니다.')
    if not isinstance(packet.get('blocks'), list) or not packet['blocks']:
        raise DraftError('본문이 없습니다.')
    for block in packet['blocks']:
        if block.get('type') == 'image':
            image = (manifest.parent / block['file']).resolve(strict=True)
            if not image.is_relative_to(manifest.parent):
                raise DraftError('초안 폴더 밖의 이미지는 업로드하지 않습니다.')
            block['path'] = str(image)
        elif block.get('type') != 'text' or not isinstance(block.get('text'), str):
            raise DraftError('지원하지 않는 본문 블록입니다.')
    return packet


def find_editor(page):
    for frame in page.frames:
        if frame.locator(TITLE).count() == 1 and frame.locator(BODY).count() > 0:
            return frame
    raise DraftError('빈 글쓰기 화면을 찾지 못했습니다. 로그인·도움말·복구 창을 확인해 주세요.')


def visible_dialog(frame):
    return frame.locator('[role="dialog"]:visible, .se-popup:visible').count() > 0


def fill_editor(page, frame, packet):
    if visible_dialog(frame):
        raise DraftError('팝업을 직접 확인하고 닫은 뒤 다시 실행해 주세요.')
    if (normalized(frame.locator(TITLE).inner_text())
            or any(normalized(t) for t in frame.locator(BODY).all_inner_texts())
            or frame.locator(IMAGES).count()):
        raise DraftError('기존 내용이 있는 글은 덮어쓰지 않습니다. 새 빈 글을 열어 주세요.')
    frame.locator(TITLE).fill(packet['title'])
    for block in packet['blocks']:
        if block['type'] == 'image':
            before = frame.locator(IMAGES).count()
            toolbar = frame.locator('button.se-image-toolbar-button:visible')
            if toolbar.count() != 1:
                raise DraftError('이미지 추가 버튼이 예상과 다릅니다. 화면을 확인해 주세요.')
            with page.expect_file_chooser() as chooser:
                toolbar.click()
            chooser.value.set_files(block['path'])
            image = frame.locator(IMAGES).nth(before)
            image.wait_for(state='visible')
            image.locator('img').last.wait_for(state='visible')
            # Require a body paragraph AFTER the image; never insert into an earlier block.
            frame.wait_for_function('''({imageSelector, bodySelector}) => {
                const images = [...document.querySelectorAll(imageSelector)];
                const body = [...document.querySelectorAll(bodySelector)].at(-1);
                return images.length && body && Boolean(images.at(-1).compareDocumentPosition(body) & 4);
            }''', arg={'imageSelector': IMAGES, 'bodySelector': BODY})
        else:
            paragraph = frame.locator(BODY).last
            paragraph.click()
            paragraph.press('ControlOrMeta+End')
            if normalized(paragraph.inner_text()):
                page.keyboard.press('Enter')
                page.keyboard.press('Enter')
            for index, line in enumerate(block['text'].split('\n')):
                if index:
                    page.keyboard.press('Enter')
                if line:
                    page.keyboard.insert_text(line)
    verify_editor(frame, packet)


def verify_editor(frame, packet):
    if normalized(frame.locator(TITLE).inner_text()) != normalized(packet['title']):
        raise DraftError('입력된 제목이 초안과 다릅니다.')
    text = normalized(' '.join(frame.locator(BODY).all_inner_texts()))
    position = 0
    for block in packet['blocks']:
        if block['type'] == 'text':
            expected = normalized(block['text'])
            found = text.find(expected, position)
            if found < 0:
                raise DraftError('입력된 본문이 초안과 다릅니다. 저장을 중단합니다.')
            position = found + len(expected)
    if frame.locator(IMAGES).count() != sum(b['type'] == 'image' for b in packet['blocks']):
        raise DraftError('이미지 수가 다릅니다. 저장을 중단합니다.')


def save_temporary(frame, timeout_ms=15000):
    if visible_dialog(frame):
        raise DraftError('팝업이 열려 있어 임시저장을 중단합니다.')
    button = frame.get_by_role('button', name=SAVE_NAME)
    if button.count() != 1 or not button.is_visible() or not button.is_enabled():
        raise DraftError('임시저장 버튼을 확실히 식별하지 못했습니다. 직접 화면을 확인해 주세요.')
    notice = frame.locator('[role="status"]:visible, [class*="toast"]:visible').filter(has_text=SUCCESS)
    if notice.count():
        raise DraftError('이전 저장 안내가 남아 있어 새 저장 결과를 구분할 수 없습니다.')
    button.click()
    # A click/open port is not proof of storage. No silent retries on an unknown outcome.
    notice.first.wait_for(state='visible', timeout=timeout_ms)


def launch(playwright, profile, browser):
    profile = Path(profile).expanduser().resolve()
    if profile == BASE or profile.is_relative_to(BASE):
        raise DraftError('로그인 프로필은 저장소 밖의 경로를 사용해 주세요.')
    profile.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(str(profile), channel=browser,
                                                          headless=False, accept_downloads=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description='네이버 임시저장 전용 도구 (발행은 직접)')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='기존 초안을 이동 가능한 폴더로 내보내기')
    source = export.add_mutually_exclusive_group(required=True)
    source.add_argument('--db', type=Path)
    source.add_argument('--json', type=Path, help='titles/blocks 등이 들어 있는 기존 봇 초안 JSON')
    export.add_argument('--draft-id', type=int)
    export.add_argument('--title-index', type=int, default=None)
    export.add_argument('--preset', type=Path, default=BASE / 'presets/travel.yaml')
    export.add_argument('--out', type=Path, required=True)
    for name in ('login', 'save'):
        sub = commands.add_parser(name)
        sub.add_argument('--profile', type=Path, default=STATE / 'browser-profile')
        sub.add_argument('--browser', choices=['chrome', 'msedge', 'chromium'],
                         default='msedge' if os.name == 'nt' else 'chrome')
        if name == 'save':
            sub.add_argument('manifest', type=Path)
            sub.add_argument('--blog-id', required=True)
    args = parser.parse_args(argv)
    if args.command == 'export':
        if args.db:
            if args.draft_id is None:
                parser.error('--db 사용 시 --draft-id가 필요합니다.')
            draft, selected = read_draft(args.db, args.draft_id)
        else:
            draft, selected = json.loads(args.json.read_text(encoding='utf-8-sig')), 0
        config = yaml.safe_load(args.preset.read_text(encoding='utf-8'))
        manifest = export_bundle(draft, selected if args.title_index is None else args.title_index, config, args.out)
        print('내보내기 완료:', manifest)
        print('preview.html을 열어 제목·본문·이미지·출처를 확인하세요.')
        return 0
    from playwright.sync_api import sync_playwright
    if args.command == 'save' and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.blog_id):
        raise DraftError('블로그 아이디 형식을 확인해 주세요.')
    packet = load_bundle(args.manifest) if args.command == 'save' else None
    receipt = None
    if packet:
        portable = {**packet, 'blocks': [{k: v for k, v in b.items() if k != 'path'} for b in packet['blocks']]}
        identity = (args.blog_id + json.dumps(portable, sort_keys=True, ensure_ascii=False)).encode()
        for block in packet['blocks']:
            if block['type'] == 'image':
                identity += Path(block['path']).read_bytes()
        receipt = Path(args.profile).resolve().parent / ('verified-' + hashlib.sha256(identity).hexdigest() + '.json')
        if receipt.exists():
            raise DraftError('이미 저장을 시도한 초안입니다. 중복 업로드를 막기 위해 중단합니다. 임시저장 목록과 기록 파일을 먼저 확인하세요.')
    with sync_playwright() as playwright:
        context = launch(playwright, args.profile, args.browser)
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            if args.command == 'login':
                page.goto('https://nid.naver.com/nidlogin.login', wait_until='domcontentloaded')
                input('브라우저에서 직접 로그인하세요. 로그인 완료 후 Enter: ')
                print('브라우저 세션을 로컬 프로필에 보관합니다. 비밀번호는 이 도구가 받지 않습니다.')
                return 0
            write_url = 'https://blog.naver.com/PostWriteForm.naver?blogId=' + args.blog_id
            page.goto(write_url, wait_until='domcontentloaded')
            input('로그인과 대상 블로그를 확인하고, 복구·도움말 창을 정리해 빈 편집기를 연 뒤 Enter: ')
            frame = find_editor(page)
            fill_editor(page, frame, packet)
            with receipt.open('x', encoding='utf-8') as file:
                json.dump({'status': 'save-attempted-unverified', 'title': packet['title']}, file, ensure_ascii=False)
            save_temporary(frame)
            print('임시저장 안내를 확인했습니다. 아직 서버 보관을 재확인하지는 않았습니다.')
            reopened = context.new_page()
            reopened.set_default_timeout(20000)
            reopened.goto(write_url, wait_until='domcontentloaded')
            input('브라우저의 임시저장 목록에서 방금 글을 다시 열고 Enter (발행하지 마세요): ')
            verify_editor(find_editor(reopened), packet)
            receipt.write_text(json.dumps({'status': 'reopened-and-verified', 'title': packet['title']},
                                          ensure_ascii=False), encoding='utf-8')
            page.close()
            print('임시글 다시 열기 검증 완료. 내용과 실제 사진·태그를 검토한 뒤 직접 발행하세요.')
            input('검토가 끝났으면 Enter를 눌러 전용 브라우저를 닫으세요: ')
            return 0
        except Exception:
            print('완료를 확인하지 못했습니다. 자동 재시도하지 않습니다. 브라우저 내용을 확인하세요.')
            input('확인 후 Enter를 누르면 브라우저를 닫습니다: ')
            raise
        finally:
            context.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        # Browser exceptions may contain private page text or request URLs.
        print(str(exc) if isinstance(exc, DraftError) else '처리 중 오류 (' + type(exc).__name__ + '). 화면과 입력 경로를 확인해 주세요.', file=sys.stderr)
        raise SystemExit(1)
