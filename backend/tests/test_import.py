import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite://"

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.parser import ParseError, parse_book


# The old Lib.ru wrapper has unclosed <option> tags and nested <pre> elements.
# Keep those details: well-formed HTML does not reproduce its extraction bugs.
LIBRU_HTML = """<html><head><title>Альбер Камю. Посторонний</title></head>
<body><pre><div align=left><form action=/INPROZ/KAMU/postoronnij.txt>
<select name=format><OPTION VALUE=".fb2.zip">Fb2.zip
<OPTION VALUE=".epub">Epub<OPTION VALUE="_Contents">Содержание
<OPTION VALUE="_with-big-pictures.html">Fine HTML
<OPTION VALUE="_with-big-pictures.html">Printed version
<OPTION VALUE="_Ascii.txt">txt(Word,КПК)<OPTION VALUE="">Lib.ru html</select>
<input type=submit value=go></form></div><pre>
<ul><a name=0></a><h2><a href=./>Альбер Камю. Посторонний</a></h2></ul>

---------------------------------------------------------------
     Перевод Н.Немчиновой
     <a href=http://example.org>OCR: Test fixture</a>
---------------------------------------------------------------

<ul><a name=1></a><h2> * ЧАСТЬ I * </h2></ul>
<ul><a name=2></a><h2>I</h2></ul>

     За окном светило солнце. Я был доволен и думал, что гости будут
здесь немного позднее. Голова уже не болела.
     Она засмеялась, и я стал ее veknb`r|. От жары кружилась cnknb`.
     Он был dnbnkem, гости asdsr ждать, а qnkmve скоро сядет.
     Melmncn позднее мы заговорили о Python, Windows и HTML.
     Он записал https://example.org/book и hello@example.org, затем abcdef.

<ul><a name=3></a><h2>II</h2></ul>

     Мы продолжили разговор.

<ul><a name=4></a><h2>V</h2></ul>

     На этом наша встреча закончилась.
<pre><hr noshade><small>Популярность: <b>229</b>, Last-modified: test fixture
</small></body></html>"""


def book_text(book):
    return "\n\n".join(chapter["text"] for chapter in book["chapters"])


class BookParserTests(unittest.TestCase):
    def test_libru_book_content_and_all_roman_chapters_are_extracted(self):
        book = parse_book("postoronnij.txt", LIBRU_HTML.encode("koi8-r"), "koi8-r")
        self.assertEqual(book["title"], "Альбер Камю. Посторонний")
        self.assertEqual([c["title"] for c in book["chapters"]], ["I", "II", "V"])
        text = book_text(book)
        self.assertTrue(text.startswith("За окном светило солнце."))
        self.assertTrue(text.endswith("На этом наша встреча закончилась."))
        for debris in ["Fb2", "Epub", "Содержание", "Fine HTML", "Printed version", "txt(Word", "Lib.ru", "Альбер Камю", "Перевод", "OCR:", "---", "Популярность", "Last-modified", "ЧАСТЬ"]:
            with self.subTest(debris=debris):
                self.assertNotIn(debris, text)
        self.assertEqual(book["total_chars"], sum(len(c["text"]) for c in book["chapters"]))
        self.assertTrue(all(c["format"] == "plain" for c in book["chapters"]))

    def test_libru_hard_wrapping_becomes_paragraphs_without_merging_dialogue(self):
        html = LIBRU_HTML.replace("     Мы продолжили разговор.", "     Первый абзац длинной строки\nпродолжается здесь.\n     -- Реплика героя.\n     -- Ответ героя.")
        chapter = parse_book("book.txt", html.encode("koi8-r"))["chapters"][1]
        self.assertEqual(chapter["text"].split("\n\n"), [
            "Первый абзац длинной строки продолжается здесь.",
            "-- Реплика героя.",
            "-- Ответ героя.",
        ])

    def test_libru_damaged_words_are_recovered_without_changing_english_or_links(self):
        text = book_text(parse_book("book.txt", LIBRU_HTML.encode("koi8-r")))
        for word in ["целовать", "голова", "доволен", "будут", "солнце", "Немного"]:
            with self.subTest(word=word):
                self.assertIn(word, text)
        for damaged in ["veknb`r|", "cnknb`", "dnbnkem", "asdsr", "qnkmve", "Melmncn"]:
            with self.subTest(damaged=damaged):
                self.assertNotIn(damaged, text)
        for value in ["Python", "Windows", "HTML", "https://example.org/book", "hello@example.org", "abcdef"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_libru_encodings_and_incorrect_http_hint(self):
        expected = parse_book("book.txt", LIBRU_HTML.encode("utf-8"))
        for encoding in ["koi8-r", "cp1251", "cp866", "utf-8", "utf-16"]:
            with self.subTest(encoding=encoding):
                actual = parse_book("book.txt", LIBRU_HTML.encode(encoding), "windows-1251")
                self.assertEqual(actual, expected)

    def test_short_authors_preface_after_bibliographic_block_is_preserved(self):
        html = LIBRU_HTML.replace('<ul><a name=1>', '     Посвящается моим друзьям.\n\n<ul><a name=1>')
        chapters = parse_book("book.txt", html.encode("koi8-r"))["chapters"]
        self.assertEqual(chapters[0]["title"], "Начало")
        self.assertEqual(chapters[0]["text"], "Посвящается моим друзьям.")

    def test_html_without_libru_wrapper_keeps_content_and_omits_page_controls(self):
        html = "<html><head><title>Название страницы</title></head><body><nav>Меню</nav><form><select><option>Выбор</option></select></form><h1>Название книги</h1><h2>I</h2><p>Текст первой главы.</p><h2>V</h2><p>Текст пятой главы.</p><footer>Подвал</footer></body></html>"
        book = parse_book("book.txt", html.encode("utf-8"))
        self.assertEqual(book["title"], "Название книги")
        self.assertEqual([c["title"] for c in book["chapters"]], ["I", "V"])
        self.assertEqual(book_text(book), "Текст первой главы.\n\nТекст пятой главы.")

    def test_plain_text_and_markdown_are_not_subject_to_libru_repair(self):
        text = "Слова veknb`r| и cnknb` в этой строке нужно сохранить без изменений."
        for filename in ["book.txt", "book.md"]:
            with self.subTest(filename=filename):
                self.assertEqual(book_text(parse_book(filename, text.encode("utf-8"))), text)
        markdown = "# Пример\n\nТекст.\n\n```python\nprint('hello')  \n```\n\n$E=mc^2$"
        book = parse_book("book.md", markdown.encode("utf-8"))
        self.assertEqual(book["chapters"][0]["format"], "markdown")
        self.assertIn("print('hello')  \n", book_text(book))

    def test_libru_without_evidence_keeps_latin_tokens(self):
        html = LIBRU_HTML.replace("veknb`r|", "Python").replace("cnknb`", "Windows")
        text = book_text(parse_book("book.txt", html.encode("koi8-r")))
        for token in ["dnbnkem", "asdsr", "qnkmve", "Melmncn", "Python", "Windows"]:
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_libru_english_prose_with_code_symbols_is_not_repaired(self):
        html = LIBRU_HTML.replace("<ul><a name=1>", "     This English sentence about Python and Windows is repeated many times. " * 30 + "\n<ul><a name=1>")
        text = book_text(parse_book("book.txt", html.encode("koi8-r")))
        for token in ["veknb`r|", "cnknb`", "dnbnkem", "Python", "Windows"]:
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_libru_page_without_book_text_is_rejected(self):
        html = '<html><body><form><select name=format><option>Lib.ru html</select></form><pre><h2>Название</h2></pre></body></html>'
        with self.assertRaises(ParseError):
            parse_book("book.txt", html.encode("utf-8"))


class BookImportApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)

        def test_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.client.close()
        self.engine.dispose()

    def assert_clean_book(self, response):
        self.assertEqual(response.status_code, 201, response.text)
        book = response.json()
        self.assertEqual([c["title"] for c in book["chapters"]], ["I", "II", "V"])
        self.assertIn("целовать", book_text(book))
        self.assertNotIn("Lib.ru", book_text(book))
        self.assertEqual(self.client.get(f'/api/books/{book["id"]}').json(), book)

    def test_url_download_charset_parsing_and_saved_book(self):
        url = "https://lib.ru/INPROZ/KAMU/postoronnij.txt"

        def serve(request):
            self.assertEqual(str(request.url), url)
            return httpx.Response(200, content=LIBRU_HTML.encode("koi8-r"), headers={"content-type": "text/html; charset=koi8-r"})

        async_client = httpx.AsyncClient
        transport = httpx.MockTransport(serve)
        with patch("backend.app.main._validate_public_url") as validate, patch(
            "backend.app.main.httpx.AsyncClient", side_effect=lambda **kwargs: async_client(transport=transport, **kwargs),
        ):
            response = self.client.post("/api/books/import-url", json={"url": url})
        validate.assert_called_once_with(url)
        self.assert_clean_book(response)

    def test_saved_libru_html_uploaded_as_txt_uses_same_parser(self):
        response = self.client.post("/api/books/upload", files={
            "file": ("postoronnij.txt", LIBRU_HTML.encode("koi8-r"), "text/plain"),
        })
        self.assert_clean_book(response)
