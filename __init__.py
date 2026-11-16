
import json
import re
from datetime import datetime
from queue import Empty, Queue
from urllib.parse import quote_plus, urljoin

from calibre.ebooks.BeautifulSoup import BeautifulSoup
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.utils.date import utc_tz
from calibre.utils.icu import lower, normalize, remove_accents_icu
from calibre.utils.localization import canonicalize_lang

BASE_URL = "https://www.kitapyurdu.com"
SEARCH_URL = BASE_URL + "/index.php?route=product/search&filter_name={query}&limit={limit}"
BOOK_URL = BASE_URL + "/kitap/-/{id}.html"
COVER_URL = "https://img.kitapyurdu.com/v1/getImage/fn:{id}"

BOOK_HREF_RE = re.compile(r"/kitap/[^/?#]*/(\d+)\.html")
COVER_ID_RE = re.compile(r"getImage/fn:(\d+)")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = [
    ("User-Agent", USER_AGENT),
    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.5"),
    ("Referer", BASE_URL + "/"),
]

LANG_TO_CODE = {
    "türkçe": "tur", "ingilizce": "eng", "ispanyolca": "spa", "italyanca": "ita",
    "korece": "kor", "rusça": "rus", "almanca": "deu", "fransızca": "fra",
    "japonca": "jpn", "arapça": "ara", "çince": "zho", "portekizce": "por",
    "yunanca": "ell", "farsça": "fas", "latince": "lat", "felemenkçe": "nld",
    "hollandaca": "nld", "lehçe": "pol", "leḣçe": "pol", "bulgarca": "bul",
    "osmanlıca": "ota", "kürtçe": "kur", "ibranice": "heb", "isveççe": "swe",
}

def tr_lower(s):
    """Türkçe uyumlu küçük harfe çevirme (İ->i, I->ı)."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def tr_title(s):
    """TÜM BÜYÜK HARF gelen metni Türkçe kurallarıyla Baş Harfi Büyük yapar."""
    out = []
    for w in tr_lower(s).split():
        first = w[0]
        if first == "i":
            first = "İ"
        elif first == "ı":
            first = "I"
        else:
            first = first.upper()
        out.append(first + w[1:])
    return " ".join(out)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def text_of(el):
    return clean(el.get_text(" ", strip=True)) if el is not None else ""


def to_int(s):
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def book_id_from_url(url):
    m = BOOK_HREF_RE.search(url or "")
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\.html", url or "")
    return m.group(1) if m else None


def parse_json_ld(soup):
    """Sayfadaki JSON-LD bloklarından Product/Book nesnesini döndürür (yoksa {})."""
    found = {}
    for tag in soup.select('script[type="application/ld+json"]'):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            if "@graph" in item and isinstance(item["@graph"], list):
                stack.extend(item["@graph"])
            t = item.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Product", "Book") for x in types):
                found.update(item)
    return found


def meta_content(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return clean(tag.get("content")) if tag and tag.get("content") else ""


class KitapyurduMetadata(object):
    def __init__(self):
        self.title = None
        self.author = None
        self.editor = None
        self.translator = None
        self.publisher = None
        self.rating = None        
        self.cover_urls = []
        self.cover_id = None
        self.desc = None
        self.date = None
        self.original_name = None
        self.isbn = None
        self.lang = None
        self.pages_num = None
        self.tags = None
        self.url = None
        self._id = None
        self.source_relevance = None

    def extra_metadata(self):
        res = ""
        if self.editor:
            res += "Editör(ler): %s<br/>" % self.editor
        if self.translator:
            res += "Çevirmen(ler): %s<br/>" % self.translator
        if self.original_name:
            res += "Orijinal Adı: %s<br/>" % self.original_name
        if self.pages_num:
            res += "Sayfa Sayısı: %s" % self.pages_num
        return "<p>%s</p>" % res if res else ""

    def to_calibre_metadata(self, append_extra=False):
        mi = Metadata(self.title or _("Unknown"), self.author or None)
        mi.identifiers = {}
        if self._id:
            mi.identifiers["kitapyurdu"] = self._id
        if self.cover_id and self.cover_id != "0":
            mi.identifiers["kitapyurdu_kapak"] = self.cover_id
        if self.isbn:
            mi.isbn = self.isbn
        if self.publisher:
            mi.publisher = self.publisher
        if self.rating:
            mi.rating = float(self.rating * 2)  
        if self.lang:
            code = LANG_TO_CODE.get(tr_lower(self.lang)) or canonicalize_lang(self.lang)
            if code:
                mi.language = code
                mi.languages = [code]
        if self.tags:
            mi.tags = sorted(self.tags)
        if self.date:
            mi.pubdate = self.date
        if self.desc:
            mi.comments = self.desc + (self.extra_metadata() if append_extra else "")
        elif append_extra and self.extra_metadata():
            mi.comments = self.extra_metadata()
        mi.source_relevance = self.source_relevance
        return mi


class KitapyurduParser(object):
    def __init__(self, browser, log, timeout=30):
        self.br = browser
        self.log = log
        self.timeout = timeout

    def fetch(self, url):
        try:
            r = self.br.open_novisit(url, timeout=self.timeout)
            content = r.read()
        except Exception as e:
            code = getattr(e, "code", None)
            if code in (403, 429, 503):
                self.log.error(
                    "Kitapyurdu erişimi engelledi (HTTP %s). Site bot koruması devreye "
                    "girmiş olabilir; bir süre bekleyip tekrar deneyin. URL: %s" % (code, url)
                )
            else:
                self.log.exception("İstek başarısız: %s (%s)" % (url, e))
            return None
        if not content:
            return None
        head = content[:4000].lower()
        if b"just a moment" in head or b"cf-chl" in head or b"attention required" in head:
            self.log.error("Bot koruması sayfası döndü, içerik alınamadı: %s" % url)
            return None
        return content

    def soup(self, url):
        content = self.fetch(url)
        return BeautifulSoup(content, "lxml") if content else None

    def search(self, query, limit=20):
        if not query:
            return []
        url = SEARCH_URL.format(query=quote_plus(query), limit=limit)
        soup = self.soup(url)
        if soup is None:
            return []
        links = (
            soup.select("#product-table div.product-cr div.name > a")
            or soup.select("div.product-cr div.name a")
            or soup.select("div.product-cr a[href]")
        )
        hrefs = [a.get("href") for a in links]
        hrefs = [h for h in hrefs if h and BOOK_HREF_RE.search(h)]
        if not hrefs:
            main = soup.select_one("#content") or soup
            hrefs = [a.get("href") for a in main.select("a[href]") if BOOK_HREF_RE.search(a.get("href", ""))]
            if hrefs:
                self.log.info("Arama için yedek seçici kullanıldı.")
        urls, seen = [], set()
        for h in hrefs:
            bid = book_id_from_url(h)
            if bid and bid not in seen:
                seen.add(bid)
                urls.append(urljoin(BASE_URL, h.split("?")[0]))
        self.log.info("'%s' için %d sonuç bulundu." % (query, len(urls)))
        return urls

    def parse_book(self, url):
        soup = self.soup(url)
        if soup is None:
            return None
        md = KitapyurduMetadata()
        md.url = url
        md._id = book_id_from_url(url)
        ld = parse_json_ld(soup)

        # Başlık
        t = soup.select_one("h1.pr_header__heading") or soup.select_one("h1")
        md.title = text_of(t) or clean(ld.get("name")) or meta_content(soup, property="og:title")
        if not md.title:
            self.log.error("Başlık bulunamadı, sayfa yapısı değişmiş olabilir: %s" % url)
            return None

        # Yazarlar
        authors = [text_of(a).replace(",", "").strip()
                   for a in soup.select("div.pr_producers__manufacturer .pr_producers__item")]
        if not authors:
            authors = [text_of(a) for a in soup.select("div.pr_producers__manufacturer a")]
        if not authors:
            ld_auth = ld.get("author")
            if isinstance(ld_auth, dict):
                ld_auth = [ld_auth]
            if isinstance(ld_auth, list):
                authors = [clean(a.get("name") if isinstance(a, dict) else str(a)) for a in ld_auth]
            elif isinstance(ld_auth, str):
                authors = [clean(ld_auth)]
        if not authors:
            scope = soup.select_one("div.pr_header") or soup.select_one("div.pr_producers") or soup
            authors = [text_of(a) for a in scope.select('a[href*="/yazar/"]')]
        md.author = list(dict.fromkeys(a for a in authors if a)) or None

        # Yayıncı
        pub = text_of(soup.select_one("div.pr_producers__publisher"))
        if not pub:
            pub = text_of(soup.select_one('a[href*="/yayinevi/"]'))
        if not pub:
            ld_pub = ld.get("publisher") or ld.get("brand")
            pub = clean(ld_pub.get("name") if isinstance(ld_pub, dict) else ld_pub)
        if pub:
            md.publisher = tr_title(pub) if pub.isupper() else pub

        # Puan (0-5)
        stars = soup.select_one("ul.pr_rating-stars")
        if stars is not None:
            md.rating = len(stars.select(".icon__star-big--selected"))
        else:
            agg = ld.get("aggregateRating")
            try:
                if isinstance(agg, dict):
                    md.rating = int(round(float(str(agg.get("ratingValue")).replace(",", "."))))
            except Exception:
                pass

        # Kapaklar
        cands = []
        for el in soup.select("div.pr_images a[href], div.pr_images img"):
            for attr in ("href", "src", "data-src", "data-original"):
                if el.get(attr):
                    cands.append(el.get(attr))
        cands.append(meta_content(soup, property="og:image"))
        img = ld.get("image")
        if isinstance(img, list):
            cands.extend(str(i) for i in img)
        elif img:
            cands.append(str(img))
        covers = []
        for c in cands:
            if not c:
                continue
            c = re.sub(r"/wh:.*$", "", urljoin(BASE_URL, c))  
            if "getImage" in c and c not in covers:
                covers.append(c)
        md.cover_urls = covers
        for c in covers:
            m = COVER_ID_RE.search(c)
            if m:
                md.cover_id = m.group(1)
                break

        # Açıklama
        d = soup.select_one("span.info__text") or soup.select_one("div.info__text")
        if d is not None:
            md.desc = str(d)
        else:
            plain = clean(ld.get("description")) or meta_content(soup, property="og:description")
            if plain:
                md.desc = "<p>%s</p>" % plain

        # Özellik tablosu (Yayın Tarihi, ISBN, Dil...)
        attrs = {}
        rows = soup.select("div.attributes tr") or soup.select("tr")
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                key = tr_lower(text_of(tds[0])).rstrip(":").strip()
                val = text_of(tds[1])
                if key and val:
                    attrs[key] = attrs[key] + ", " + val if key in attrs else val
        if not attrs:
            body = soup.get_text("\n", strip=True)
            for key in ("ISBN", "Dil", "Sayfa Sayısı", "Yayın Tarihi", "Çevirmen", "Orijinal Adı", "Editör"):
                m = re.search(re.escape(key) + r":?\s*\n?\s*([^\n]+)", body)
                if m:
                    attrs[tr_lower(key)] = clean(m.group(1))

        def attr(*names):
            for n in names:
                v = attrs.get(tr_lower(n))
                if v:
                    return v
            return None

        md.editor = attr("Editör", "Editor")
        md.translator = attr("Çevirmen")
        md.original_name = attr("Orijinal Adı")
        md.lang = attr("Dil")
        md.pages_num = to_int(attr("Sayfa Sayısı"))

        raw_date = attr("Yayın Tarihi")
        if raw_date:
            for fmt in ("%d.%m.%Y", "%m.%Y", "%Y"):
                try:
                    md.date = datetime.strptime(raw_date.strip(), fmt).replace(tzinfo=utc_tz)
                    break
                except ValueError:
                    continue

        raw_isbn = attr("ISBN") or clean(ld.get("isbn") or ld.get("gtin13"))
        if raw_isbn:
            md.isbn = check_isbn(raw_isbn)

        # Etiketler
        tags = {text_of(s) for s in soup.select("ul.rel-cats__list span")}
        if not tags:
            tags = {text_of(a) for a in soup.select("ul.rel-cats__list a")}
        tags.discard("")
        tags -= {"Kitap", "Diğer"}
        md.tags = tags or None
        return md

class Kitapyurdu(Source):
    name = "Kitapyurdu"
    author = "Nezih <https://github.com/anezih> (1.2.0 düzeltmeleri)"
    description = _("Downloads metadata and covers from kitapyurdu.com")
    version = (1, 2, 0)
    minimum_calibre_version = (6, 10, 0)
    supported_platforms = ["windows", "osx", "linux"]
    capabilities = frozenset(["identify", "cover"])
    touched_fields = frozenset([
        "title", "authors", "tags", "publisher", "comments", "pubdate",
        "rating", "identifier:isbn", "language", "identifier:kitapyurdu",
    ])
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True
    has_html_comments = True
    prefer_results_with_isbn = False
    can_get_multiple_covers = False

    options = (
        Option(
            "entries_per_search_result_page", "choices", 10,
            _("Max. number of search results."),
            _("Select max. number of search results to fetch. More results = slower, "
              "and a higher chance of being rate-limited by the site."),
            {5: "5", 10: "10", 20: "20", 25: "25"},
        ),
        Option(
            "append_extra_metadata_to_desc", "bool", False,
            _("Append extra metadata to the end of the description."),
            _("Extra metadata: Editor(s), translator(s), original name, page number."),
        ),
    )
    def make_browser(self):
        br = self.browser
        try:
            br.set_handle_robots(False)
        except Exception:
            pass
        br.addheaders = list(HEADERS)
        return br

    def get_book_url_name(self, idtype, idval, url):
        return "Kitapyurdu"

    def get_book_url(self, identifiers):
        ky_id = identifiers.get("kitapyurdu")
        if ky_id:
            return ("kitapyurdu", ky_id, BOOK_URL.format(id=ky_id))
        return None

    def id_from_url(self, url):
        m = re.search(r"kitapyurdu\.com/kitap/[^/]*/(\d+)\.html", url or "")
        return ("kitapyurdu", m.group(1)) if m else None

    def build_query(self, log, title=None, authors=None, only_title=False,
                    rm_accents=False, ss=False, sj=True):
        if not (title or authors):
            return None
        title_tokens = list(self.get_title_tokens(title=title, strip_subtitle=ss, strip_joiners=sj))
        author_tokens = []
        if not only_title:
            author_tokens = list(self.get_author_tokens(authors=authors, only_first_author=True))
        q = normalize(lower(" ".join(title_tokens + author_tokens)))
        if rm_accents:
            q = remove_accents_icu(q)
        q = q.strip()
        if q:
            log.info("Constructed query: %s" % q)
            return q
        return None

    # ---- identify ---------------------------------------------------------
    def identify(self, log, result_queue, abort, title=None, authors=None,
                 identifiers=None, timeout=30):
        identifiers = identifiers or {}
        max_res = int(self.prefs.get("entries_per_search_result_page") or 10)
        append_extra = bool(self.prefs.get("append_extra_metadata_to_desc"))
        parser = KitapyurduParser(self.make_browser(), log, timeout)

        results, seen = [], set()

        def add(md):
            if md and md._id not in seen:
                seen.add(md._id)
                results.append(md)

        ky_id = identifiers.get("kitapyurdu")
        if ky_id:
            add(parser.parse_book(BOOK_URL.format(id=ky_id)))
            if results:
                log.info("Matched kitapyurdu id.")

        if not results:
            queries = []
            isbn = check_isbn(identifiers.get("isbn"))
            if isbn:
                queries.append(isbn)
            queries.append(self.build_query(log, title=title, authors=authors))
            queries.append(self.build_query(log, title=title, authors=authors, only_title=True))
            queries.append(self.build_query(log, title=title, authors=authors, only_title=True,
                                            rm_accents=True, ss=True))
            tried = set()
            for q in queries:
                if abort.is_set():
                    return
                if not q or q in tried:
                    continue
                tried.add(q)
                for url in parser.search(q, limit=20)[:max_res]:
                    if abort.is_set():
                        return
                    try:
                        add(parser.parse_book(url))
                    except Exception:
                        log.exception("Sayfa ayrıştırılamadı: %s" % url)
                if results:
                    break

        if not results:
            log.info("Kitapyurdu'nda sonuç bulunamadı.")
            return

        for relevance, md in enumerate(results, start=1):
            md.source_relevance = relevance
            mi = md.to_calibre_metadata(append_extra)
            if md.cover_urls and md._id:
                self.cache_identifier_to_cover_url(md._id, md.cover_urls[0])
            if md.isbn and md._id:
                self.cache_isbn_to_identifier(md.isbn, md._id)
            result_queue.put(mi)

    def get_cached_cover_url(self, identifiers):
        identifiers = identifiers or {}
        cover_id = identifiers.get("kitapyurdu_kapak")
        if cover_id and cover_id != "0":
            return COVER_URL.format(id=cover_id)
        ky_id = identifiers.get("kitapyurdu")
        if ky_id:
            return self.cached_identifier_to_cover_url(ky_id)
        return None

    def download_cover(self, log, result_queue, abort, title=None, authors=None,
                       identifiers=None, timeout=30, get_best_cover=False):
        identifiers = identifiers or {}
        if abort.is_set():
            return
        url = self.get_cached_cover_url(identifiers)
        if not url:
            log.info("Could not find cached URL for covers, running identify...")
            queue = Queue()
            self.identify(log, queue, abort, title=title, authors=authors,
                          identifiers=identifiers, timeout=timeout)
            if abort.is_set():
                return
            found = []
            while True:
                try:
                    found.append(queue.get_nowait())
                except Empty:
                    break
            found.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in found:
                url = self.get_cached_cover_url(mi.identifiers)
                if url:
                    break
        if not url:
            log.info("Kitapyurdu'nda kapak bulunamadı.")
            return
        try:
            data = self.make_browser().open_novisit(url, timeout=timeout).read()
            if data:
                result_queue.put((self, data))
        except Exception:
            log.exception("Failed to get covers from: %s" % url)
