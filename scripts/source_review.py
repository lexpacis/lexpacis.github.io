"""Check the original article for topic context and the issuing publisher."""
from html.parser import HTMLParser
import re
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from core import parse_date, plain
from network_policy import RestrictedRedirect, validate_public_url
from publication_policy import OFFICIAL_SOURCES, article_origin, official_origin, restricted_content


_VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
_SKIP = {'script','style','nav','noscript','footer','aside','form','button'}
_HIDDEN = {'script','style','template','noscript','svg'}
_BLOCK = {'article','aside','br','div','dt','dd','footer','h1','h2','header','li','main','p','section','td','th'}
_SOURCE_META = {'contentsource','articlesource','source'}
_CONTEXT_META = {'articletitle','og:title','twitter:title','description','og:description','twitter:description','keywords'}
_ATTRIBUTION = re.compile(r'(?:来\s*源\s*[:：]|转\s*载\s*(?:自|于))\s*([^\n]*)')
_BODY = re.compile(r'\b(?:entry-content|post-content|article-content|article__body|article-body|'
                   r'news-details|trs_ueditor|field--name-body|articletext|newscontent|news-content|artibody)\b',re.I)


class ArticleEvidence(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.bodies = []
        self.meta = {}
        self.canonical = ''
        self.headline = []
        self.page_text = []
        self.sources = []
        self.metadata_context = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'meta':
            name = fields.get('name') or fields.get('property')
            if name and fields.get('content'):
                name, value = name.casefold(), fields['content'].strip()
                self.meta.setdefault(name,value)
                if value and name in _SOURCE_META:
                    self.sources.append(value)
                if name in _CONTEXT_META:
                    self.metadata_context.append(value)
        if tag == 'link' and (fields.get('rel') or '').lower() == 'canonical':
            self.canonical = fields.get('href') or ''
        if tag in _BLOCK:
            self.page_text.append('\n')
        if tag in _VOID:
            return
        candidate = None
        if tag == 'article' or _BODY.search(' '.join((fields.get('class') or '',fields.get('id') or ''))):
            candidate = []
            self.bodies.append(candidate)
        self.stack.append((tag,candidate))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self.page_text.append('\n')
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not any(tag in _HIDDEN for tag,_ in self.stack):
            self.page_text.append(re.sub(r'\s+',' ',data))
        if any(tag in _SKIP for tag,_ in self.stack):
            return
        for tag,text in self.stack:
            if text is not None:
                text.append(data)
            if tag=='h1':
                self.headline.append(data)

    def context(self):
        return ' '.join(' '.join(values).strip() for values in self.bodies if values).strip()

    def attributions(self):
        visible = [match.strip() for match in _ATTRIBUTION.findall(''.join(self.page_text))]
        return self.sources + visible


def inspect_html(entry, source, url, markup):
    parse = ArticleEvidence()
    parse.feed(markup)
    canonical = urljoin(url,parse.canonical) if parse.canonical else url
    if not article_origin(source,canonical):
        return entry
    body = parse.context()
    if len(body)<120:
        return entry
    head = ' '.join(parse.headline).strip() or parse.meta.get('og:title','')
    page_title = parse.meta.get('articletitle','') or head
    date,precision = parse_date(parse.meta.get('pubdate') or parse.meta.get('article:published_time') or '')
    basis = '原文正文核对'
    bibliography = dict(entry.get('bibliography',{}))
    if source['id'] in OFFICIAL_SOURCES:
        if not (official_origin(source,url) and official_origin(source,canonical)):
            return entry
        declared = parse.attributions()
        if source['id']=='china-mfa-statements':
            issuer = '中华人民共和国外交部'
            if (parse.meta.get('sitename')!=issuer or not page_title.startswith('外交部发言人')
                or not head.startswith('外交部发言人')
                or any(found not in ('外交部',issuer) for found in declared)):
                return entry
        else:
            if not declared or set(declared)-{'新华社','新华网'}:
                return entry
            issuer = declared[0]
        bibliography['original_issuer'] = issuer
        basis = '官方原发页面核对'
    evidence = ' '.join((entry['title'],entry.get('evidence') or '',head,body,*parse.metadata_context))
    return {**entry,'url':url,'title':plain(page_title) or entry['title'],'evidence':evidence,
            'published_at':date or entry.get('published_at'),'date_precision':precision if date else entry.get('date_precision','unknown'),
            'evidence_basis':basis,'bibliography':bibliography}


def verify_entries(entries, source, config, rules):
    if source['kind']=='crossref':
        return entries
    reviewed=[]
    official=source['id'] in OFFICIAL_SOURCES
    requests_left=config.get('max_article_checks_per_source',4)
    blocked_hosts=set()
    for entry in entries:
        url=entry['url']
        if not article_origin(source,url):
            continue
        if not official and restricted_content((entry['title'],url,entry.get('evidence')),rules):
            reviewed.append(entry)
            continue
        hostname=urlsplit(url).hostname
        if requests_left<=0 or hostname in blocked_hosts:
            continue
        requests_left-=1
        try:
            validate_public_url(url,{hostname})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),RestrictedRedirect({hostname}))
            request=urllib.request.Request(url,headers={'User-Agent':config['user_agent'],'Accept':'text/html'})
            with opener.open(request,timeout=min(config['fetch_timeout_seconds'],7)) as response:
                if 'html' not in response.headers.get('Content-Type',''):
                    reviewed.append(entry)
                    continue
                raw=response.read(1_500_001)
                final=response.url
                if len(raw)>1_500_000 or not article_origin(source,final):
                    reviewed.append(entry)
                    continue
                markup=raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
            result=inspect_html(entry,source,final,markup)
            if result['url']!=entry['url']:
                # Keep a single permanent URL so an old entry cannot survive
                # a later decision to hold the redirected record.
                result=entry
            reviewed.append(result)
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,429,503):
                blocked_hosts.add(hostname)
        except (OSError,ValueError):
            continue
    return reviewed
