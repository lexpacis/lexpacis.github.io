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
from notice_dates import EXACT_DAY, day, event_date, structured_events, submission_deadline
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
_WIX_EVENTS = {'afsil-events','sladi-events','ccil-events'}
_EXACT_ARTICLE_PATHS = _WIX_EVENTS | {'asiansil-events','jsil-events','itlos-docket','ilc-reports','cambridge-books',
                                    'afronomics-analysis','asil-insights','un-news-global','opcw-news','afchpr-cases',
                                    'au-treaties','ila-events'}
PDF_BASIS = '官方 PDF 正文核对'
REVIEW_COUNTS = ('candidates','checked','verified','china_hold','origin_hold','body_hold',
                 'access_hold','unsupported_hold','pdf_hold','pending','directory_count','first_http_status')


def review_exempt(source):
    """征稿启事是书目信息，不是新闻。

    A call-for-papers page routinely lists its editors and their institutions
    ("University of Macao", "City University of Hong Kong"). Screening the whole
    body would reject every call that happens to have a scholar from those
    places on its board, which says nothing about the topic of the call.
    Titles stay screened: a call about China is still held by the title rule.
    """
    return source.get('module')=='cfp'


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
        self.citation_authors = []

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
                if name == 'citation_author':
                    self.citation_authors.append(value)
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
            or (self.source_id in _WIX_EVENTS and tag == 'main' and fields.get('id') == 'PAGES_CONTAINER')
            or (self.source_id == 'itlos-docket' and 'fs-content-container' in (fields.get('class') or '').split())
            or (self.source_id == 'ilc-reports' and fields.get('id') == 'content')
            or (self.source_id == 'icsid-cases' and 'case__detail' in (fields.get('class') or '').split())
            or (self.source_id == 'au-treaties' and fields.get('id') == 'block-au-main-content')
            or (self.source_id == 'un-news-global' and fields.get('id') in {
                'block-views-block-content-fields-block-lead','block-views-block-content-fields-block-body'})
            or (self.source_id == 'cambridge-books' and tag == 'section' and 'book-overview' in (fields.get('class') or '').split())
            or (self.source_id and self.source_id.startswith('cambridge-cfp-') and fields.get('id')=='MAIN_CONTENT')
        )
        generic_body=tag=='article' or _BODY.search(' '.join((fields.get('class') or '',fields.get('id') or '')))
        if official_body or (self.source_id!='au-treaties' and generic_body):
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
        allowed={'form'} if self.source_id=='anzsil-events' else {'aside'} if self.source_id=='au-treaties' else ()
        if any(tag in _SKIP and tag not in allowed for tag,_ in self.stack):
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
    source_id=source['id']
    parse = ArticleEvidence(source_id)
    parse.feed(markup)
    canonical = urljoin(url,parse.canonical) if parse.canonical else url
    if not article_origin(source,canonical):
        return entry
    if source_id in _EXACT_ARTICLE_PATHS and any(not re.fullmatch(source['link_pattern'],urlsplit(link).path)
                                                 for link in (url,canonical)):
        return entry
    if source_id in _EXACT_ARTICLE_PATHS and urlsplit(url).path!=urlsplit(canonical).path:
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
    authors=entry.get('authors',[])
    if source_id in _WIX_EVENTS:
        page_title=parse.meta.get('og:title','').split(' | ')[0] or entry['title']
    if source_id=='jsil-events':
        page_title=entry['title']
    if source_id=='un-news-global':
        page_title=parse.meta.get('og:title') or entry['title']
    if source_id=='ila-events' and not parse.headline:
        return entry
    if source_id=='au-treaties':
        value=re.search(r'\bDate of Adoption</div>\s*<div[^>]*>\s*<time datetime="(\d{4}-\d{2}-\d{2})',markup)
        adopted=day(value[1]) if value else None
        if not adopted or not page_title:
            return entry
        bibliography.update(type='treaty',adopted=adopted,publisher='African Union')
    if source.get('notice_kind')=='journal':
        page_title=entry['title']
    if source_id=='ilc-reports':
        report=re.search(r'<h3>(Report on the work of the[^<]{1,140})</h3>',markup)
        year=urlsplit(url).path.strip('/').rsplit('/',1)[-1]
        if not report or not report[1].endswith(f'({year})'):
            return entry
        page_title='International Law Commission · '+plain(report[1])
        bibliography['type']='report'
        date,precision=parse_date(year)
    if source_id=='cambridge-books':
        page_title=plain(parse.meta.get('citation_title',''))
        publisher=parse.meta.get('citation_publisher','')
        authors=list(dict.fromkeys(plain(name) for name in parse.citation_authors if plain(name)))
        specs={}
        for matched in re.finditer(r'<dt\b[^>]*>\s*(Publication date|ISBN):\s*</dt>((?:\s*<dd\b[^>]*>[^<>]{0,55}</dd>){1,5})',markup,re.I):
            specs.setdefault(matched[1].lower(),[]).extend(plain(value) for value in re.findall(r'<dd\b[^>]*>([^<>]*)</dd>',matched[2],re.I))
        isbn=next((value for value in specs.get('isbn',[]) if re.fullmatch(r'97[89]\d{10}',value)),None)
        dates=sorted({parsed for value in specs.get('publication date',[]) if (parsed:=parse_date(value)[0])})
        if not (page_title and authors and isbn and dates and publisher=='Cambridge University Press'):
            return entry
        date,precision=parse_date(dates[0])
        bibliography.update(type='book',publisher=publisher,ISBN=isbn)
        body+=' '+parse.meta.get('citation_abstract','')
    if source.get('notice_kind') in ('event','journal'):
        date_candidates=[]
        if source['id']=='mpil-events':
            sidebar=re.search(r'<div\b[^>]*\bclass=["\x27][^"\x27]*\bgreenBackground\b[^"\x27]*["\x27][^>]*>.{0,1800}?<strong>(.{1,140}?)</strong>',
                              markup,re.I|re.S)
            if sidebar:
                date_candidates.append(plain(sidebar[1]))
        if source['id']=='asil-events' and not re.search(r'\bASIL Event\b',body,re.I):
            return entry
        structured=structured_events(markup,page_title) if source_id in _WIX_EVENTS | {'asil-events','asiansil-events'} else ()
        if source_id=='ccil-events' and re.fullmatch(r'/wm\d+-20\d{2}',urlsplit(url).path):
            if match:=re.search(r'\b(?:venue|Québec City)\s*(?:[|·:/]\s*)?([A-Za-z]+ \d{1,2}\s*[-–]\s*\d{1,2},? 20\d{2})',body,re.I):
                date_candidates.append(match[1])
        if source_id in {'afsil-events','asiansil-events'}:
            match=re.search(r'(?:Annual Conference in [^.!?]{1,75},\s*on|conference will be hosted by [^.!?]{1,90} on)\s+(\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s+[A-Za-z]+\s+20\d{2})',body,re.I)
            if match:
                date_candidates.append(match[1])
        if source_id=='jsil-events':
            match=re.search(r'〔日時〕\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日[（(][^）)]{1,5}[）)](?:・\d{1,2}日[（(][^）)]{1,5}[）)])*',body)
            if match:
                year,month,start=map(int,match.groups())
                last=int(re.findall(r'(\d{1,2})日',match[0])[-1])
                structured=[{'startDate':f'{year:04d}-{month:02d}-{start:02d}',
                             'endDate':f'{year:04d}-{month:02d}-{last:02d}'}]
        if source_id=='sladi-events':
            match=re.search(r'conferencia bienal del (\d{1,2}) de ([a-z]+) al (\d{1,2}) de ([a-z]+) de (20\d{2})\b',body,re.I)
            if match and not re.search(r'conferencia\s+(?:cancelada|aplazada|pospuesta)',body,re.I):
                months='enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre'.split()
                first,m1,last,m2,year=match.groups()
                if m1.lower() in months and m2.lower() in months:
                    structured=[{'startDate':f'{year}-{months.index(m1.lower())+1:02d}-{int(first):02d}',
                                 'endDate':f'{year}-{months.index(m2.lower())+1:02d}-{int(last):02d}'}]
        if source_id=='ila-events':
            date_candidates.extend(match[1] for match in re.finditer(r'\bDate:\s*(\d{1,2}\s+[A-Za-z]+\s+20\d{2})\b',body))
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
            'evidence_basis':basis,'bibliography':bibliography,'authors':authors}


def official_pdf_issuer(source,url):
    parsed=urlsplit(url)
    if not article_origin(source,url):
        return None
    if (source['id']=='icj' and parsed.netloc=='www.icj-cij.org' and not parsed.query
        and _ICJ_PDF.fullmatch(parsed.path)):
        return r'international court of justice|cour internationale de justice'
    if (source['id']=='echr-press' and parsed.netloc=='hudoc.echr.coe.int'
        and parsed.path=='/app/conversion/pdf/' and re.fullmatch(r'id=003-\d+-\d+&library=ECHR',parsed.query)):
        return r'european court of human rights|cour europ[ée]enne des droits de l.homme'
    if (source['id']=='iacthr-press' and parsed.netloc=='www.corteidh.or.cr' and not parsed.query
        and re.fullmatch(r'/docs/comunicados/cp_\d+_20\d{2}_ENG\.pdf',parsed.path)):
        return r'inter.american court of human rights|corte interamericana de derechos humanos'
    if (source['id']=='itlos' and parsed.netloc=='www.itlos.org' and not parsed.query
        and re.fullmatch(r'/fileadmin/itlos/documents/press_releases_english/PR_\d+_[A-Za-z]{2}\.pdf',parsed.path)):
        return r'international tribunal for the law of the sea|tribunal international du droit de la mer'
    if (source['id']=='un-depositary' and parsed.netloc=='treaties.un.org' and not parsed.query
        and re.fullmatch(r'/doc/Publication/CN/(20\d{2})/CN\.\d+\.\1-Eng\.pdf',parsed.path)):
        return r'united nations.{0,120}depositary|depositary.{0,120}united nations'
    return None


def inspect_official_pdf(entry,source,url,raw):
    issuer=official_pdf_issuer(source,url)
    if not issuer or not raw.startswith(b'%PDF-'):
        return entry
    # Use the standard Ubuntu reader, without running document actions or scripts.
    # Scan the whole document, with bounded size, page count and runtime.
    max_pages=int(source.get('max_pdf_pages',12))
    max_bytes=int(source.get('max_pdf_bytes',2_000_000))
    read_seconds=int(source.get('max_pdf_seconds',5))
    prefix=['prlimit','--as=536870912',f'--fsize={max_bytes}','--','env','LC_ALL=C']
    try:
        info=subprocess.run([*prefix,'pdfinfo','-'],input=raw,capture_output=True,check=True,timeout=6).stdout
        pages=re.search(rb'(?m)^Pages:\s+(\d+)\s*$',info)
        if not pages or not 1<=int(pages[1])<=max_pages:
            return entry
        with tempfile.TemporaryFile() as output:
            subprocess.run([*prefix,'pdftotext','-enc','UTF-8','-','-'],input=raw,
                           stdout=output,stderr=subprocess.DEVNULL,check=True,timeout=read_seconds)
            output.seek(0)
            text=output.read(4_000_000).decode('utf-8')
    except (OSError,subprocess.SubprocessError,UnicodeError):
        return entry
    leafs=text.split('\f')
    if leafs[-1].strip()=='':
        leafs.pop()
    short=sum(1 for page in leafs if len(page.strip())<100)
    if len(leafs)!=int(pages[1]) or short:
        return entry
    body=' '.join(text.split())
    if not re.search(issuer,body,re.I):
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
        pdf=official_pdf_issuer(source,url) is not None
        accept='application/pdf' if pdf else 'text/html'
        limit=int(source.get('max_pdf_fetch_bytes',3_000_000)) if pdf else 1_500_000
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
                result=inspect_official_pdf(entry,source,url,raw)
            else:
                result=inspect_html(entry,source,url,raw.decode(encoding,errors='replace'))
            if result is entry:
                report['pdf_hold' if pdf else 'body_hold']+=1
            elif not official and not review_exempt(source) and restricted_content(result['evidence'],rules):
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
