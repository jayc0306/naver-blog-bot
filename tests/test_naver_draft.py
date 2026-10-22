import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

import naver_draft as naver


class DraftBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        executable = os.environ.get('TEST_CHROMIUM_PATH')
        cls.browser = cls.playwright.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        draft = {'titles': [{'text': '가을 축제 <검토>'}], 'thumbnail_text': '가을 축제',
                 'blocks': [{'type': 'text', 'content': '첫 안내입니다.\n날짜는 직접 확인하세요.'},
                            {'type': 'table', 'content': '준비물|겉옷'},
                            {'type': 'text', 'content': '마지막 안내입니다.'}],
                 'tags': ['가을축제'], 'fact_checks': []}
        self.draft = draft
        self.manifest = naver.export_bundle(draft, 0, {'card_colors': ['#111111', '#FFD400']}, self.root / 'bundle')
        self.packet = naver.load_bundle(self.manifest)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        fixture = (Path(__file__).parent / 'fixtures/naver_editor.html').read_text(encoding='utf-8')
        self.page.route('http://fixture.test/**', lambda route: route.fulfill(body=fixture, content_type='text/html'))
        self.page.goto('http://fixture.test/')
        self.page.set_default_timeout(2000)

    def tearDown(self):
        self.context.close()
        self.tmp.cleanup()

    def test_fill_save_reopen_keeps_image_and_text_order_without_publish(self):
        naver.fill_editor(self.page, self.page.main_frame, self.packet)
        order = self.page.locator('#article').evaluate('''node => [...node.children].map(child =>
            child.classList.contains('se-image') ? 'image' : child.innerText.trim()).filter(Boolean)''')
        self.assertEqual(order[0], 'image')
        self.assertIn('첫 안내입니다.', order[1])
        self.assertEqual(order[2], 'image')
        self.assertEqual(order[3], '마지막 안내입니다.')
        naver.save_temporary(self.page.main_frame)
        self.page.reload()
        naver.verify_editor(self.page.main_frame, self.packet)
        self.assertIsNone(self.page.evaluate('localStorage.getItem("published")'))

    def test_existing_article_is_unchanged(self):
        self.page.locator(naver.TITLE).fill('기존 글')
        self.page.locator(naver.BODY).fill('지우면 안 되는 내용')
        with self.assertRaises(naver.DraftError):
            naver.fill_editor(self.page, self.page.main_frame, self.packet)
        self.assertEqual(self.page.locator(naver.TITLE).inner_text(), '기존 글')
        self.assertIsNone(self.page.evaluate('localStorage.getItem("saved")'))

    def test_publish_button_is_never_used_as_save_fallback(self):
        self.page.locator('#save').evaluate('node => node.remove()')
        with self.assertRaises(naver.DraftError):
            naver.save_temporary(self.page.main_frame)
        self.assertIsNone(self.page.evaluate('localStorage.getItem("published")'))

    def test_ambiguous_save_button_is_not_clicked(self):
        self.page.locator('#save').evaluate('node => node.after(node.cloneNode(true))')
        with self.assertRaises(naver.DraftError):
            naver.save_temporary(self.page.main_frame)
        self.assertIsNone(self.page.evaluate('localStorage.getItem("saved")'))

    def test_popup_blocks_input(self):
        self.page.evaluate('document.body.insertAdjacentHTML("beforeend", "<div role=dialog>복구하시겠습니까?</div>")')
        with self.assertRaises(naver.DraftError):
            naver.fill_editor(self.page, self.page.main_frame, self.packet)
        self.assertEqual(self.page.locator(naver.TITLE).inner_text(), '')

    def test_bundle_cannot_upload_files_outside_its_folder(self):
        outside = self.root / 'private.png'
        outside.write_bytes(b'private')
        packet = json.loads(self.manifest.read_text(encoding='utf-8'))
        packet['blocks'][0]['file'] = '../private.png'
        self.manifest.write_text(json.dumps(packet), encoding='utf-8')
        with self.assertRaises(naver.DraftError):
            naver.load_bundle(self.manifest)

    def test_export_preserves_existing_directory_and_escapes_preview(self):
        preview = (self.manifest.parent / 'preview.html').read_text(encoding='utf-8')
        self.assertIn('&lt;검토&gt;', preview)
        with self.assertRaises(FileExistsError):
            naver.export_bundle(self.draft, 0, {}, self.manifest.parent)

    def test_readonly_database_export_uses_selected_title(self):
        database = self.root / 'draft.db'
        with sqlite3.connect(database) as conn:
            conn.execute('CREATE TABLE drafts(id INTEGER, body TEXT, title_idx INTEGER)')
            conn.execute('INSERT INTO drafts VALUES(1, ?, 0)', (json.dumps(self.draft),))
        before = database.read_bytes()
        self.assertEqual(naver.read_draft(database, 1), (self.draft, 0))
        self.assertEqual(before, database.read_bytes())
        with self.assertRaises(naver.DraftError):
            naver.read_draft(database, 99)

    def test_reopen_rejects_changed_content(self):
        naver.fill_editor(self.page, self.page.main_frame, self.packet)
        self.page.locator(naver.BODY).last.fill('다른 글')
        with self.assertRaises(naver.DraftError):
            naver.verify_editor(self.page.main_frame, self.packet)

    def test_missing_save_confirmation_is_failure_and_not_retried(self):
        self.page.locator('#save').evaluate('''node => node.onclick = () => {
            localStorage.setItem('attempts', String(Number(localStorage.getItem('attempts') || 0) + 1));
        }''')
        with self.assertRaises(BrowserTimeout):
            naver.save_temporary(self.page.main_frame, timeout_ms=250)
        self.assertEqual(self.page.evaluate('localStorage.getItem("attempts")'), '1')
        self.assertIsNone(self.page.evaluate('localStorage.getItem("published")'))

    def test_manual_login_resumes_writer_in_same_browser_session(self):
        fixture = (Path(__file__).parent / 'fixtures/naver_editor.html').read_text(encoding='utf-8')
        def writer(route):
            if 'fixture_session=present' in (route.request.header_value('cookie') or ''):
                route.fulfill(body=fixture, content_type='text/html')
            else:
                route.fulfill(body='<script>location.replace("https://nid.naver.com/nidlogin.login")</script>',
                              content_type='text/html')
        self.page.route('http://fixture.test/protected', writer)
        self.page.route('https://nid.naver.com/**', lambda route: route.fulfill(body='<h1>Login fixture</h1>'))
        self.page.goto('http://fixture.test/protected')
        self.page.wait_for_url('https://nid.naver.com/nidlogin.login')
        self.assertTrue(naver.is_login_page(self.page))
        prompts = []
        def manual_login(message):
            prompts.append(message)
            self.context.add_cookies([{'name': 'fixture_session', 'value': 'present', 'url': 'http://fixture.test'}])
            return ''
        frame = naver.wait_for_editor(self.page, 'http://fixture.test/protected',
                                      prompt=manual_login, timeout_ms=100)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(frame.locator(naver.TITLE).count(), 1)
        self.assertFalse(naver.is_login_page(self.page))
        self.assertIsNone(self.page.evaluate('localStorage.getItem("saved")'))

    def test_cancelled_login_is_not_treated_as_editor_access(self):
        self.page.route('https://nid.naver.com/**', lambda route: route.fulfill(body='<h1>Login fixture</h1>'))
        self.page.goto('https://nid.naver.com/nidlogin.login')
        with self.assertRaises(naver.DraftError):
            naver.wait_for_editor(self.page, 'http://fixture.test/protected', prompt=lambda _: 'q', timeout_ms=100)
        self.assertTrue(naver.is_login_page(self.page))

    def test_login_redirect_loop_stops_after_one_manual_attempt(self):
        self.page.route('http://fixture.test/protected', lambda route: route.fulfill(
            body='<script>location.replace("https://nid.naver.com/nidlogin.login")</script>', content_type='text/html'))
        self.page.route('https://nid.naver.com/**', lambda route: route.fulfill(body='<h1>Login fixture</h1>'))
        self.page.goto('http://fixture.test/protected')
        self.page.wait_for_url('https://nid.naver.com/nidlogin.login')
        prompts = []
        def unchanged_login(message):
            prompts.append(message)
            return ''
        with self.assertRaisesRegex(naver.DraftError, '반복 시도를 멈춥니다'):
            naver.wait_for_editor(self.page, 'http://fixture.test/protected',
                                  prompt=unchanged_login, timeout_ms=100)
        self.assertEqual(len(prompts), 1)

    def test_unknown_editor_is_not_reloaded_or_overwritten(self):
        self.page.set_content('<main>원래 화면</main>')
        before_url = self.page.url
        with self.assertRaises(naver.DraftError):
            naver.wait_for_editor(self.page, 'http://fixture.test/protected', prompt=lambda _: 'q', timeout_ms=100)
        self.assertEqual(self.page.url, before_url)
        self.assertEqual(self.page.locator('main').inner_text(), '원래 화면')


if __name__ == '__main__':
    unittest.main()
