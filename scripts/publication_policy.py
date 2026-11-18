"""Conservative editorial screening for automatically published metadata."""
import re
import unicodedata
import hashlib
import html
from pathlib import Path
from urllib.parse import unquote, urlsplit


APPROVED_JOURNALS = {
    'crossref-ejil': '0938-5428',
    'crossref-ajil': '0002-9300',
    'crossref-ljil': '0922-1565',
}
REGIONAL_JOURNALS = {
    **APPROVED_JOURNALS,
    'crossref-iclq': '0020-5893',
    'crossref-asianjil': '2044-2513',
    'crossref-jiel': '1369-3034',
    'crossref-wtr': '1474-7456',
    'crossref-cyil': '0069-0058',
    'crossref-aybil': '0084-7658',
    'crossref-ajicl': '0954-8890',
    'crossref-amdi': '1870-4654',
    'crossref-ijmcl': '0927-3522',
    'crossref-irrc': '1816-3831',
    'crossref-icsidreview': '0258-3690',
}
SCOPE_REVISION = hashlib.sha256(''.join(Path(__file__).with_name(name).read_text(encoding='utf-8')
                                     for name in ('publication_policy.py','source_review.py','notice_dates.py')).encode()).hexdigest()
DOI = re.compile(r'10\.\d{4,9}/[a-z0-9._;()/:-]+', re.I)


_CJK = re.compile(
    r"中国|中國|中華|中华|中共|中方|涉华|涉華|对华|對華|"
    r"台湾|台灣|臺灣|台海|臺海|两岸|兩岸|香港|港澳|澳门|澳門|"
    r"新疆|西藏|藏区|藏區|维吾尔|維吾爾|南海|东海|東海|钓鱼岛|釣魚島|"
    r"北京|上海|深圳|广州|廣州|大陆|大陸|澎湖|金门|金門|马祖|馬祖|"
    r"台北|臺北|高雄|台独|臺獨|一國兩制|一国两制|國安法|国安法|"
    r"拉萨|拉薩|驻藏|駐藏|藏南|东突|東突|港独|港獨|"
    r"达旺|達旺|屋脊|麦克马洪|麥克馬洪|强迫劳动|強迫勞動|兵团|兵團|"
    r"港岛|港島|九龙|九龍|新界|一带一路|一帶一路|中印边境|中印邊境|中印实际控制线|中印實際控制線|"
    r"乌鲁木齐|烏魯木齊|喀什|和田|伊犁|内蒙古|內蒙古|"
    r"西沙|南沙|东沙|東沙|黄岩岛|黃岩島|仁爱礁|仁愛礁|太平岛|太平島"
)
_MULTILINGUAL = re.compile(
    r"(?<![^\W\d_])(?:"
    r"chin(?:a|e|es[ea]|ois(?:e|es)?|[eê]s|[eê]sa|[eê]ses|esisch\w*)|chino(?:s)?|"
    r"prc|p[. ]r[. ]c\.?|beijing|peking|shanghai|shenzhen|guangzhou|cantonese|"
    r"taiwan(?:ese)?|taipei|formos(?:a|an)|kaohsiung|kinmen|quemoy|matsu(?:[ -]+islands?)?|penghu|pescadores|roc|"
    r"cross[ -]+strait|taiwan[ -]+strait|hong[ -]*kong|hongkonger\w*|hksar|kowloon|macau|macao|"
    r"xinjiang|sinkiang|uyghurs?|uighurs?|uygurs?|uigurs?|xuar|urumqi|kashgar|hotan|"
    r"east(?:ern)?[ -]+turk[ei]st[áa]n|dogu[ -]+turkistan|"
    r"tibet(?:an|ans)?|tib[ée]tain\w*|xizang|lhasa|dhara[ms]{1,2}sh?ala|amdo|kham|"
    r"inner[ -]+mongolia|south[ -]+mongolia|south(?:ern)?[ -]+tibet|"
    r"arunachal(?:[ -]+pradesh)?|tawang|mcmahon[ -]+line|aksai[ -]+chin|line[ -]+of[ -]+actual[ -]+control|"
    r"belt[ -]+and[ -]+road[ -]+initiative|people.s[ -]+liberation[ -]+army|"
    r"x[pb]cc|uflpa|production[ -]+and[ -]+construction[ -]+corps|uyghur[ -]+forced[ -]+labou?r|"
    r"sino[ -]\w+|sinophone|sinic(?:ization|isation|ize|ise)|"
    r"ccp|c[. ]c[. ]p\.?|"
    r"senkaku|diaoyu|spratlys?|paracels?|scarborough[ -]+shoal|second[ -]+thomas[ -]+shoal|"
    r"pratas(?:[ -]+islands?)?|(?:nine|ten|eleven|9|10|11)[ -]+dash(?:ed)?|"
    r"(?:dotted|u[ -]+shaped)[ -]+line|"
    r"dalai[ -]+lama|one[ -]+country[ -]+two[ -]+systems|"
    r"кита[йяею]|китайск\w*|тайван\w*|гонконг\w*"
    r")(?![^\W\d_])"
)
_OTHER_SCRIPTS = re.compile(r"الصين|تايوان|هونغ كونغ|التبت|شينجيانغ|대만|중국|홍콩|티베트|"
                            r"タイワン|チベット|シンチヤン|"
                            r"trung quoc|đai loan|hong kong|tay tang|tan cuong")


def strict_scope(rules):
    return rules.get('publication_scope') == 'academic_metadata'


def regional_scope(rules):
    return rules.get('publication_scope') == 'verified_origins'


def managed_scope(rules):
    return strict_scope(rules) or regional_scope(rules)


OFFICIAL_SOURCES = {
    'china-mfa-statements': ('china_government','www.mfa.gov.cn','/fyrbt_673021/dhdw_673027/'),
    'china-xinhua-politics': ('china_official_media','www.news.cn','/politics/'),
}


def official_origin(source, url):
    rule = OFFICIAL_SOURCES.get(source['id'])
    if not rule:
        return False
    authority, host, path = rule
    listing, article = urlsplit(source['url']), urlsplit(url)
    return (source.get('authority') == authority and listing.scheme == article.scheme == 'https'
            and listing.netloc == article.netloc == host and listing.path == path
            and article.path.startswith(path) and bool(re.fullmatch(source['link_pattern'],article.path)))


def original_publisher(source, item):
    issuer = item['bibliography'].get('original_issuer')
    expected = {'china-mfa-statements':{'中华人民共和国外交部'},
                'china-xinhua-politics':{'新华社','新华网'}}
    return (item.get('evidence_basis')=='官方原发页面核对' and official_origin(source,item['url'])
            and issuer in expected.get(source['id'],()))


def article_origin(source, url):
    listing, article = urlsplit(source['url']), urlsplit(url)
    return (article.scheme=='https' and not article.username and not article.password
            and article.netloc in {listing.netloc, *source.get('article_hosts',())}
            and bool(article.path.strip('/')))


def regional_item(item, source, rules, reviewed):
    if not source.get('enabled') or source_paused(source,rules) or not item['title'].strip():
        return False
    proof = item.get('evidence') or ''
    if source['id'] in OFFICIAL_SOURCES:
        return bool(original_publisher(source,item) and (reviewed or len(proof)>=80))
    if source.get('kind')=='crossref':
        if not eligible_metadata(item,source) or (not reviewed and len(proof)<60):
            return False
    else:
        from source_review import PDF_BASIS, case_pdf_issuer
        basis=item.get('evidence_basis')
        verified_format=(basis=='原文正文核对' or (basis==PDF_BASIS and case_pdf_issuer(source,item['url'])))
        if not article_origin(source,item['url']) or not verified_format or (not reviewed and len(proof)<120):
            return False
    if item.get('module')=='cfp':
        from notice_dates import item_notices
        if source.get('notice_kind')!='journal' or not source.get('journal_id') or not item_notices(item)[1]:
            return False
    fields=[item['title'],item['url'],item.get('doi'),item['authors'],item['bibliography'],proof]
    return not restricted_content(fields,rules)


def approved_source(source):
    issn = REGIONAL_JOURNALS.get(source['id'])
    url = urlsplit(source['url'])
    return bool(issn and source['kind'] == 'crossref' and source['authority'] == 'scholarly_index'
                and source['module'] == 'research' and url.scheme == 'https'
                and url.netloc == 'api.crossref.org' and url.path == f'/journals/{issn}/works')


def eligible_metadata(item, source):
    bibliography = item['bibliography']
    doi = item.get('doi') or ''
    return bool(approved_source(source) and DOI.fullmatch(doi)
                and item['url'] == 'https://doi.org/' + doi
                and item['module'] == 'research' and item['title'].strip()
                and item['authors'] and item['published_at']
                and bibliography.get('type') == 'journal-article'
                and REGIONAL_JOURNALS[source['id']] in (bibliography.get('ISSN') or ())
                and bibliography.get('container-title'))


def snapshot_issues(data, sources, rules):
    if regional_scope(rules):
        return regional_snapshot_issues(data,sources,rules)
    if not strict_scope(rules):
        return []
    if not isinstance(data, dict) or data.get('publication_scope') != 'academic_metadata':
        return ['公开快照缺少当前学术书目规则']
    approved = {source['id']: source for source in sources if not source_paused(source, rules)}
    issues = []
    if any(source.get('id') not in approved for source in data['sources']):
        issues.append('公开快照含未获准的来源')
    if data['cfp'].get('calls'):
        issues.append('公开快照含未获准的征稿公告')
    for item in data['items']:
        source = approved.get(item['source_id'])
        if (not source or not eligible_metadata(item, source)
            or item.get('screened_policy') != SCOPE_REVISION
            or item['id'] in rules.get('hidden_items', ())
            or 'evidence' in item or item.get('title_zh') or item.get('summary_zh')
            or any(item.get(key) for key in ('ai', 'event', 'deadline'))
            or restricted_content(item, rules)):
            issues.append('公开快照含未通过现行规则的条目')
            break
    return issues


def regional_snapshot_issues(data, sources, rules):
    if not isinstance(data,dict) or data.get('publication_scope')!='verified_origins':
        return ['公开快照缺少当前来源核验规则']
    registries={source['id']:source for source in sources}
    from notice_dates import item_notices
    items={}
    for item in data['items']:
        source=registries.get(item['source_id'])
        if (not source or item.get('screened_policy')!=SCOPE_REVISION or not regional_item(item,source,rules,True)
            or item['id'] in rules.get('hidden_items',()) or 'evidence' in item
            or any(item.get(key) for key in ('title_zh','summary_zh','ai'))
            or (item.get('event'),item.get('deadline'))!=item_notices(item)):
            return ['公开快照含未通过来源核验的条目']
        items[item['id']]=item
    for call in data['cfp']['calls']:
        parent=items.get(call.get('parent_item_id'))
        if not parent or call['url']!=parent['url'] or restricted_content(call,rules):
            return ['公开快照含未通过来源核验的征稿线索']
        if call.get('verification')=='primary_notice':
            source=registries[parent['source_id']]
            deadline=item_notices(parent)[1]
            expected='journal_special' if parent['module']=='cfp' else 'conference'
            journal_id=source.get('journal_id') if expected=='journal_special' else None
            if (not deadline or call.get('kind')!=expected or call.get('journal_id')!=journal_id
                or (call.get('deadline'),call.get('deadline_text'),call.get('deadline_label'))!=
                   (deadline['date'],deadline['date_text'],deadline['label'])):
                return ['公开快照含未经核验的征稿截止日']
        elif call.get('deadline') or call.get('kind')!='discovery':
            return ['公开快照含未经核验的征稿公告']
    return []


def source_paused(source, rules):
    return (source['id'] in rules.get('paused_sources', ())
            or source['id'] in rules.get('hidden_sources', ())
            or source['module'] in rules.get('paused_modules', ())
            or (strict_scope(rules) and (source['id'] not in APPROVED_JOURNALS or not approved_source(source))))


def _normalized(value):
    for _ in range(3):
        decoded = html.unescape(unquote(value))
        if decoded == value: break
        value = decoded
    value = unicodedata.normalize('NFKC', value).casefold()
    value = ''.join(character for character in unicodedata.normalize('NFKD', value)
                    if unicodedata.category(character) not in {'Mn', 'Cf'})
    return re.sub(r'[\s_‐‑‒–—−·/]+', ' ', value)


def restricted_content(value, rules):
    """Inspect full accessible text and nested metadata, independently of presentation."""
    if not (managed_scope(rules) or rules.get('screen_china_related_materials')):
        return False
    if isinstance(value, str):
        normalized = _normalized(value)
        compact_cjk = re.sub(r'[\s.\-]+', '', normalized)
        return bool(_CJK.search(compact_cjk) or _MULTILINGUAL.search(normalized)
                    or _OTHER_SCRIPTS.search(normalized))
    if isinstance(value, dict):
        return any(restricted_content(field, rules) for field in value.values())
    if isinstance(value, (tuple, list)):
        return any(restricted_content(field, rules) for field in value)
    return False
