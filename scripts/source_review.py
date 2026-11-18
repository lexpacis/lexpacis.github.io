"""Check the original article for topic context and the issuing publisher."""
from html.parser import HTMLParser
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from core import identity, parse_date, plain
from network_policy import public_opener
from notice_dates import EXACT_DAY, event_date, structured_events, submission_deadline
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
_ICJ_PDF = re.compile(r'/sites/default/files/(?:press-releases|case-related)/\d+/\d+-\d{8}-pre-\d+-\d+-en\.pdf')
PDF_BASIS = '官方 PDF 正文核对'
REVIEW_COUNTS = ('candidates','checked','verified','china_hold','origin_hold','body_hold',
                 'access_hold','unsupported_hold','pdf_hold','pending','directory_count','first_http_status')


def clean_review(value):
    if not isinstance(value,dict):
        return {}
    result={name:value[name] for name in REVIEW_COUNTS if type(value.get(name)) is int and 0<=value[name]<=10000}
    cursor=value.get('cursor','')
    if isinstance(cursor,str) and re.fullmatch('[a-f0-9]{20}',cursor):
        result['cursor']=cursor
    failure=value.get('failure','')
    if isinstance(failure,str) and re.fullmatch(r'http_[45]\d{2}|invalid_xml|parse_failed|network_error',failure):
        result['failure']=failure
    if value.get('fallback') is True:
        result['fallback']=True
    return result


class ArticleEvidence(HTMLParser):
    def __init__(self, source_id=None):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.bodies = []
        self.meta = {}
        self.canonical = ''
        self.headline = []
        self.page_text = []
        self.sources = []
        self.metadata_context = []
        self.source_id = source_id
        self.pca_after_title = False
        self.pca_paragraphs = []

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
        official_body = (
            (self.source_id == 'wto' and fields.get('id') == 'mainContent')
            or (self.source_id == 'pca' and (tag == 'main' or fields.get('role') == 'main'))
            or (self.source_id == 'icj-pending' and fields.get('id') == 'block-icj-content')
            or (self.source_id == 'mpil-events' and 'article' in (fields.get('class') or '').split())
            or (self.source_id == 'anzsil-events' and 'WaGadgetEventsStateDetails' in (fields.get('class') or '').split())
            or (self.source_id and self.source_id.startswith('cambridge-cfp-') and fields.get('id')=='MAIN_CONTENT')
        )
        if tag == 'article' or official_body or _BODY.search(' '.join((fields.get('class') or '',fields.get('id') or ''))):
            candidate = []
            self.bodies.append(candidate)
        self.stack.append((tag,candidate))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.source_id == 'pca' and tag == 'h1':
            self.pca_after_title = True
        if tag in _BLOCK:
            self.page_text.append('\n')
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not any(tag in _HIDDEN for tag,_ in self.stack):
            self.page_text.append(re.sub(r'\s+',' ',data))
        if any(tag in _SKIP and not (tag=='form' and self.source_id=='anzsil-events') for tag,_ in self.stack):
            return
        if self.pca_after_title and any(tag=='p' for tag,_ in self.stack):
            self.pca_paragraphs.append(data)
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
    parse = ArticleEvidence(source['id'])
    parse.feed(markup)
    canonical = urljoin(url,parse.canonical) if parse.canonical else url
    if not article_origin(source,canonical):
        return entry
    body = parse.context()
    head = ' '.join(parse.headline).strip() or parse.meta.get('og:title','')
    page_title = parse.meta.get('articletitle','') or head
    if len(body)<120 and source['id']=='pca':
        clean=lambda value: re.sub(r'[^\w]+',' ',plain(value).casefold()).strip()
        if head and clean(head)==clean(entry['title']):
            paragraphs=' '.join(' '.join(parse.pca_paragraphs).split())
            if re.search(r'permanent court of arbitration|arbitrat\w+|conciliation',paragraphs,re.I):
                body=paragraphs
    if len(body)<120:
        return entry
    date,precision = parse_date(parse.meta.get('pubdate') or parse.meta.get('article:published_time') or '')
    basis = '原文正文核对'
    bibliography = dict(entry.get('bibliography',{}))
    if source.get('notice_kind') in ('event','journal'):
        date_candidates=[]
        if source['id']=='mpil-events':
            sidebar=re.search(r'<div\b[^>]*\bclass=["\x27][^"\x27]*\bgreenBackground\b[^"\x27]*["\x27][^>]*>.{0,1800}?<strong>(.{1,140}?)</strong>',
                              markup,re.I|re.S)
            if sidebar:
                date_candidates.append(plain(sidebar[1]))
        if source['id']=='asil-events' and not re.search(r'\bASIL Event\b',body,re.I):
            return entry
        structured=structured_events(markup,page_title) if source['id']=='asil-events' else ()
        if source['id']=='anzsil-events':
            calendar_fields={}
            for part in ('Start','End'):
                block=re.search(rf'class=["\x27]eventInfo{part}Date["\x27][^>]*>(.{{0,900}}?)</li>',markup,re.I|re.S)
                if block and (match:=EXACT_DAY.search(plain(block[1]))):
                    calendar_fields[part.lower()+'Date']=match[0]
            structured=[calendar_fields]
        calendar=event_date(body,structured,date_candidates) if source['notice_kind']=='event' else None
        deadline=submission_deadline(entry['title'],body)
        if calendar or deadline:
            bibliography['notice_dates']={'event':calendar,'deadline':deadline}
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


def icj_pdf_url(source,url):
    parsed=urlsplit(url)
    return (source['id']=='icj' and article_origin(source,url) and not parsed.query
            and bool(_ICJ_PDF.fullmatch(parsed.path)))


def inspect_icj_pdf(entry,source,url,raw):
    if not icj_pdf_url(source,url) or not raw.startswith(b'%PDF-'):
        return entry
    # Use the standard Ubuntu reader, without running document actions or scripts.
    # Scan the entire short press release, with bounded size, page count and runtime.
    prefix=['prlimit','--as=268435456','--fsize=2000000','--','env','LC_ALL=C']
    try:
        info=subprocess.run([*prefix,'pdfinfo','-'],input=raw,capture_output=True,check=True,timeout=4).stdout
        pages=re.search(rb'(?m)^Pages:\s+(\d+)\s*$',info)
        if not pages or not 1<=int(pages[1])<=12:
            return entry
        with tempfile.TemporaryFile() as output:
            subprocess.run([*prefix,'pdftotext','-enc','UTF-8','-','-'],input=raw,
                           stdout=output,stderr=subprocess.DEVNULL,check=True,timeout=5)
            output.seek(0)
            text=output.read(2_000_000).decode('utf-8')
    except (OSError,subprocess.SubprocessError,UnicodeError):
        return entry
    leafs=text.split('\f')
    if leafs[-1].strip()=='':
        leafs.pop()
    if len(leafs)!=int(pages[1]) or any(len(page.strip())<100 for page in leafs):
        return entry
    body=' '.join(text.split())
    if not re.search(r'international court of justice|cour internationale de justice',body,re.I):
        return entry
    evidence=' '.join((entry['title'],entry.get('evidence') or '',body))
    return {**entry,'evidence':evidence,'evidence_basis':PDF_BASIS}


def verify_entries(entries, source, config, rules, report=None):
    if source['kind']=='crossref':
        return entries
    if report is None:
        report={}
    cursor=report.get('cursor','')
    report.update(dict.fromkeys(REVIEW_COUNTS,0))
    report['candidates']=len(entries)
    reviewed,eligible=[],[]
    official=source['id'] in OFFICIAL_SOURCES
    for entry in entries:
        url=entry['url']
        if not article_origin(source,url):
            report['origin_hold']+=1
            continue
        if not official and restricted_content((entry['title'],url,entry.get('evidence')),rules):
            report['china_hold']+=1
            reviewed.append(entry)
            continue
        eligible.append(entry)
    backfill=eligible[1:]
    offset=next((i+1 for i,e in enumerate(backfill) if identity(e['url'])==cursor),0)
    budget=source.get('article_checks',config.get('max_article_checks_per_source',4))
    blocked_hosts=set()
    for index,entry in enumerate(eligible[:1]+backfill[offset:]+backfill[:offset]):
        url=entry['url']
        hostname=urlsplit(url).hostname
        if report['checked']>=budget or hostname in blocked_hosts:
            continue
        report['checked']+=1
        if index:
            report['cursor']=identity(url)
        pdf=icj_pdf_url(source,url)
        accept='application/pdf' if pdf else 'text/html'
        limit=3_000_000 if pdf else 1_500_000
        try:
            opener=public_opener({hostname})
            request=urllib.request.Request(url,headers={'User-Agent':config['user_agent'],'Accept':accept})
            with opener.open(request,timeout=min(config['fetch_timeout_seconds'],7)) as response:
                content=response.headers.get_content_type()
                if content not in (('application/pdf',) if pdf else ('text/html','application/xhtml+xml')):
                    report['unsupported_hold']+=1
                    reviewed.append(entry)
                    continue
                raw=response.read(limit+1)
                if len(raw)>limit or response.url!=url:
                    report['origin_hold']+=1
                    reviewed.append(entry)
                    continue
                encoding=response.headers.get_content_charset() or 'utf-8'
            if pdf:
                result=inspect_icj_pdf(entry,source,url,raw)
            else:
                result=inspect_html(entry,source,url,raw.decode(encoding,errors='replace'))
            if result is entry:
                report['pdf_hold' if pdf else 'body_hold']+=1
            elif not official and restricted_content(result['evidence'],rules):
                report['china_hold']+=1
            else:
                report['verified']+=1
            reviewed.append(result)
        except urllib.error.HTTPError as exc:
            report['access_hold']+=1
            if not report['first_http_status']:
                report['first_http_status']=exc.code
            if exc.code in (401,403,429,503):
                blocked_hosts.add(hostname)
        except (OSError,ValueError,LookupError):
            report['access_hold']+=1
    report['pending']=len(eligible)-report['checked']
    return reviewed
