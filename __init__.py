import re
from datetime import datetime
from queue import Empty, Queue
from urllib.parse import quote_plus

import mechanize
from calibre.ebooks.BeautifulSoup import BeautifulSoup
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.utils.icu import lower, normalize, remove_accents_icu, title_case

# ENTRIES_PER_SEARCH_RESULT_PAGE = 20

lang_to_eng = {
    "Türkçe"     : "Turkish",
    "İngilizce"  : "English",
    "İspanyolca" : "Spanish",
    "İtalyanca"  : "Italian",
    "Korece"     : "Korean",
    "Rusça"      : "Russian",
    "Almanca"    : "German",
    "Fransızca"  : "French"
}

class KitapyurduMetadata():
    title: str = None
    author: list[str] = None
    editor: str = None
    translator: str = None
    publisher: str = None
    rating: int = None
    cover_url: list = None
    desc: str = None
    date: datetime = None
    original_name: str = None
    isbn: str = None
    lang: str = None
    pages_num: int = None
    tags: set[str] = None
    url: str = None
    _id: str = None
    cover_id: str = None
    source_relevance: int = None

    def extra_metadata(self):
        res = f""
        if self.editor:
            res += f"Editör(ler): {self.editor}<br/>"
        if self.translator:
            res += f"Çevirmen(ler): {self.translator}<br/>"
        if self.original_name:
            res += f"Orijinal Adı: {self.original_name}<br/>"
        if self.pages_num:
            res += f"Sayfa Sayısı: {self.pages_num}"
        if res:
            res = f"<p>{res}</p>"
        return res

    def to_calibre_metadata(self, append_extra: bool = False):
        mi = Metadata(
            title=self.title,
            authors=self.author
        )
        mi.identifiers = {}
        mi.identifiers["kitapyurdu"] = self._id
        if self.cover_id != "0":
            mi.identifiers["kitapyurdu_kapak"] = self.cover_id
        if self.isbn:
            mi.isbn = self.isbn
        if self.publisher:
            mi.publisher = self.publisher
        if self.rating >= 0:
            mi.rating = self.rating
        if self.lang:
            eng = lang_to_eng.get(self.lang)
            if eng:
                mi.language = eng
        if self.tags:
            mi.tags = list(self.tags)
        if self.date:
            mi.pubdate = self.date
        if self.desc:
            mi.comments = f"{self.desc}{self.extra_metadata() if append_extra else ''}"
        mi.source_relevance = self.source_relevance
        return mi

class KitapyurduMetadataParser():
    def __init__(self, query, limit, logger, identifers: dict = {}) -> None:
        self.query = query
        self.max_results = limit
        self.logger = logger
        self.br = mechanize.Browser()
        self.br.set_handle_robots(False)
        self.br.addheaders = [
            ('User-Agent', 'APIs-Google (+https://developers.google.com/webmasters/APIs-Google.html)'),
        ]
        self.ky_ident = None
        ky_ident = identifers.get("kitapyurdu")
        if ky_ident:
            self.ky_ident = ky_ident

    def url_content_from_query(self, query, limit):
        quoted = quote_plus(query)
        _url = f"https://www.kitapyurdu.com/index.php?route=product/search&filter_name={quoted}&limit={limit}"
        try:
            r = self.br.open(_url)
            content = r.read()
            r.close()
            return content
        except Exception as e:
            self.logger.exception(f"Failed to get search results, exception: {e}\nURL was: {_url}")
            return None

    def search_urls(self, soup):
        table = soup.select_one("#product-table")
        if table:
            products = table.select("div.product-cr")
            links = [l.select_one("div.name > a") for l in products]
            return [link["href"] for link in links]
        else:
            return []

    def url_content(self, u):
        try:
            r = self.br.open(u)
            content = r.read()
            r.close()
            return content
        except Exception as e:
            self.logger.exception(f"Failed to get page content, exception: {e}\nURL was: {u}")
            return None

    def get_search_page_urls(self, q, lim=20):
        content = self.url_content_from_query(query=q, limit=lim)
        if content:
            soup = BeautifulSoup(content, "lxml")
            urls = self.search_urls(soup)
            if urls:
                return urls
            else:
                return []
        else:
            return []

    def parse_pages(self, only_ident: bool = False):
        if only_ident:
            u = f"https://www.kitapyurdu.com/kitap/-/{self.ky_ident}.html"
            soups = [(BeautifulSoup(self.url_content(u), "lxml"), u)]
        else:
            soups = [(BeautifulSoup(self.url_content(u), "lxml"), u) for u in self.get_search_page_urls(q=self.query, lim=self.max_results) if u]
        metadata_list = []
        if not soups:
            return metadata_list
        for soup in soups:
            metadata = KitapyurduMetadata()
            metadata.url = soup[1]

            id_re = re.search("(\d+)\.html", metadata.url)
            if id_re.lastindex > 0:
                metadata._id = id_re.group(1)

            title = soup[0].select_one("h1.pr_header__heading")
            if title:
                metadata.title = title.getText()

            author = soup[0].select("div.pr_producers__manufacturer > div.pr_producers__item")
            if author:
                metadata.author = [a.getText().replace(",","").strip() for a in author]

            publisher = soup[0].select_one("div.pr_producers__publisher")
            if publisher:
                # all_caps = publisher.getText().strip()
                # all_caps = all_caps.replace("I","ı").replace("İ", "i")
                metadata.publisher = title_case(publisher.getText().strip())

            rating_ul = soup[0].select_one("ul.pr_rating-stars")
            if rating_ul:
                rating = len(rating_ul.select(".icon__star-big--selected"))
                metadata.rating = rating

            cover_url_with_res = soup[0].select_one("div.pr_images")
            if cover_url_with_res:
                multi = cover_url_with_res.select_one("ul.pr_images__thumb-list")
                if multi:
                    metadata.cover_url = [(x := a["href"])[:x.index("wh") - 1] for a in multi.find_all("a")]
                else:
                    jbox = cover_url_with_res.select_one("a.js-jbox-book-cover")["href"]
                    cover_url = jbox[:jbox.index("wh") - 1]
                    metadata.cover_url = [cover_url]

            if metadata.cover_url:
                metadata.cover_id = metadata.cover_url[0].split(":")[-1]

            desc = soup[0].select_one("span.info__text")
            if desc:
                metadata.desc = str(desc)

            attrs_table = {}
            table = soup[0].select_one("div.attributes").find_all("tr")
            tds = [td for t in table for td in t.find_all("td")]
            for td in range(len(tds)):
                if td % 2 == 1:
                    key = tds[td-1].getText().strip()
                    val = tds[td].getText()
                    if key not in attrs_table.keys():
                        attrs_table[key] = val
                    else:
                        attrs_table[key] += f", {val}"

            editors = attrs_table.get("Editor:")
            if editors:
                metadata.editor = editors.strip()

            translators = attrs_table.get("Çevirmen:")
            if translators:
                metadata.translator = translators.strip()

            date = attrs_table.get("Yayın Tarihi:")
            if date:
                metadata.date = datetime.strptime(date, "%d.%m.%Y")

            original_name = attrs_table.get("Orijinal Adı:")
            if original_name:
                metadata.original_name = original_name

            isbn = attrs_table.get("ISBN:")
            if isbn:
                metadata.isbn = isbn

            lang = attrs_table.get("Dil:")
            if lang:
                # lang = lang.replace("I","ı").replace("İ", "i")
                metadata.lang = title_case(lang)

            pages_num = attrs_table.get("Sayfa Sayısı:")
            if pages_num:
                metadata.pages_num = int(pages_num)

            tags_ul = soup[0].select_one("ul.rel-cats__list")
            tags = {}
            if tags_ul:
                tags = {t.getText() for t in tags_ul.find_all("span")}
            if tags:
                if "Kitap" in tags:
                    tags.remove("Kitap")
                if "Diğer" in tags:
                    tags.remove("Diğer")
                metadata.tags = tags
            metadata_list.append(metadata)
        return metadata_list

class Kitapyurdu(Source):
    name                    = "Kitapyurdu"
    author                  = "Nezih <https://github.com/anezih>"
    description             = _("Downloads metadata and covers from kitapyurdu.com")
    version                 = (1, 1, 1)
    minimum_calibre_version = (6, 10, 0)
    supported_platforms     = ["windows", "osx", "linux"]
    capabilities            = frozenset(["identify", "cover"])
    touched_fields          = frozenset(
        [
            "title", "authors", "tags", "publisher", "comments", "pubdate",
            "rating", "identifier:isbn", "language", "identifier:kitapyurdu"
        ]
    )
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True
    has_html_comments = True
    prefer_results_with_isbn = False
    can_get_multiple_covers = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_res = self.prefs.get("entries_per_search_result_page")
        self.append_extra = self.prefs.get("append_extra_metadata_to_desc")

    options = (
        Option (
            "entries_per_search_result_page",
            "choices",
            20,
            _("Max. number of search results."),
            _("Select max. number of search results. (50 may cause timeout errors.)"),
            {20:"20", 25:"25", 50:"50"}
        ),
        Option (
            "append_extra_metadata_to_desc",
            "bool",
            False,
            _("Append extra metadata to the end of the description."),
            _("Extra metadata: Editor(s), translator(s), original name, page number.")
        ),
    )

    def get_book_url_name(self, idtype, idval, url):
        return "Kitapyurdu"

    def get_book_url(self, identifiers):
        kitapyurdu_id = identifiers.get("kitapyurdu")
        if kitapyurdu_id:
            url = f"https://www.kitapyurdu.com/kitap/-/{kitapyurdu_id}.html"
            return ("kitapyurdu", kitapyurdu_id, url)
        else:
            return None

    def build_query(self, log, title=None, authors=None, only_title=False, rm_accents=False, ss=False, sj=True):
        title_tokens = []
        author_tokens = []
        if title or authors:
            title_tokens = list(self.get_title_tokens(title=title, strip_subtitle=ss, strip_joiners=sj))
            if not only_title:
                author_tokens = list(self.get_author_tokens(authors=authors, only_first_author=True))
            all = lower(" ".join(title_tokens + author_tokens))
            all = normalize(all)
            if rm_accents:
                all = remove_accents_icu(all)
                log.info(f"Removed accents from query.")
            if all:
                log.info(f"Constructed query: {all}")
                return all
            else:
                return None

    def create_metadata_list(self, log, title=None, authors=None, identifiers={}):
        metadata_list: list[KitapyurduMetadata] = []
        ky_ident = identifiers.get("kitapyurdu")
        if ky_ident:
            ky_metadata_obj = KitapyurduMetadataParser(query=None, identifers=identifiers, limit=self.max_res, logger=log)
            metadata_list = ky_metadata_obj.parse_pages(only_ident=True)
            if metadata_list:
                log.info(f"{'-'*30}\nMatched kitapyurdu id.")
                return metadata_list
        title_authors = self.build_query(log=log, title=title, authors=authors)
        ky_metadata_obj = KitapyurduMetadataParser(query=title_authors, limit=self.max_res, logger=log)
        metadata_list = ky_metadata_obj.parse_pages()
        if metadata_list:
            return metadata_list
        else:
            log.info(f"Build query second pass: only_title, strip_subtitle, rm_accents")
            title_authors = self.build_query(log=log, title=title, authors=authors, only_title=True, rm_accents=True, ss=True)
            ky_metadata_obj = KitapyurduMetadataParser(query=title_authors, limit=self.max_res, logger=log)
            metadata_list = ky_metadata_obj.parse_pages()
            if metadata_list:
                return metadata_list
            else:
                return None

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        if abort.is_set():
            return
        metadata_list = self.create_metadata_list(log=log, title=title, authors=authors, identifiers=identifiers)
        if not metadata_list:
            return
        if metadata_list:
            for relevance, mi in enumerate(metadata_list, start=1):
                mi.source_relevance = relevance
                result_queue.put(mi.to_calibre_metadata(self.append_extra))

    def get_cached_cover_url(self, identifiers):
        _id = identifiers.get('kitapyurdu_kapak')
        if _id:
            return f"https://img.kitapyurdu.com/v1/getImage/fn:{_id}"
        else:
            return None

    def download_cover(self, log, result_queue, abort, title=None, authors=None, identifiers=None, timeout=30, get_best_cover=False):
        if abort.is_set():
            return
        cached = self.get_cached_cover_url(identifiers=identifiers)
        if cached:
            try:
                cover_data = self.browser.open_novisit(cached, timeout=timeout).read()
                if cover_data:
                    result_queue.put((self, cover_data))
            except:
                log.exception(f"Failed to get covers from: {cached}")
        else:
            log.info("Could not find cached URL for covers, running identify...")
            cached_from_ident = ""
            queue = Queue()
            self.identify(log, result_queue=queue, abort=abort, title=title, authors=authors, identifiers=identifiers, timeout=30)
            if abort.is_set():
                return
            res = []
            while True:
                try:
                    res.append(queue.get_nowait())
                except Empty:
                    break
            res.sort(
                key=self.identify_results_keygen(
                    title=title,
                    authors=authors,
                    identifiers=identifiers
                )
            )
            for mi in res:
                cached = self.get_cached_cover_url(mi.identifiers)
                if cached:
                    cached_from_ident = cached
                    break
            try:
                cover_data = self.browser.open_novisit(cached_from_ident, timeout=timeout).read()
                if cover_data:
                    result_queue.put((self, cover_data))
            except:
                log.exception(f"Failed to get covers from: {cached}")
