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
SCOPE_REVISION = hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()
DOI = re.compile(r'10\.\d{4,9}/[a-z0-9._;()/:-]+', re.I)


_CJK = re.compile(
    r"中国|中國|中華|中华|中共|中方|涉华|涉華|对华|對華|"
    r"台湾|台灣|臺灣|台海|臺海|两岸|兩岸|香港|港澳|澳门|澳門|"
    r"新疆|西藏|藏区|藏區|维吾尔|維吾爾|南海|东海|東海|钓鱼岛|釣魚島|"
    r"北京|上海|深圳|广州|廣州|大陆|大陸|澎湖|金门|金門|马祖|馬祖|"
    r"台北|臺北|高雄|台独|臺獨|一國兩制|一国两制|國安法|国安法|"
    r"拉萨|拉薩|驻藏|駐藏|东突|東突|港独|港獨|"
    r"乌鲁木齐|烏魯木齊|喀什|和田|伊犁|内蒙古|內蒙古|"
    r"西沙|南沙|东沙|東沙|黄岩岛|黃岩島|仁爱礁|仁愛礁|太平岛|太平島"
)
_MULTILINGUAL = re.compile(
    r"(?<![^\W\d_])(?:"
    r"chin(?:a|e|es[ea]|ois(?:e|es)?|[eê]s|[eê]sa|[eê]ses|esisch\w*)|chino(?:s)?|"
    r"prc|p[. ]r[. ]c\.?|beijing|peking|shanghai|shenzhen|guangzhou|cantonese|"
    r"taiwan(?:ese)?|taipei|formos(?:a|an)|kaohsiung|kinmen|quemoy|matsu(?:[ -]+islands?)?|penghu|pescadores|roc|"
    r"cross[ -]+strait|taiwan[ -]+strait|hong[ -]*kong|hongkonger\w*|hksar|kowloon|macau|macao|"
    r"xinjiang|sinkiang|uyghurs?|uighurs?|u[yï]ghur\w*|xuar|urumqi|kashgar|hotan|"
    r"east(?:ern)?[ -]+turkest[áa]n|tibet(?:an|ans)?|tib[ée]tain\w*|xizang|lhasa|dharamsala|"
    r"inner[ -]+mongolia|south[ -]+mongolia|"
    r"sino[ -]\w+|sinophone|sinic(?:ization|isation|ize|ise)|"
    r"ccp|c[. ]c[. ]p\.?|"
    r"senkaku|diaoyu|spratlys?|paracels?|scarborough[ -]+shoal|second[ -]+thomas[ -]+shoal|"
    r"pratas(?:[ -]+islands?)?|nine[ -]+dash|(?:dotted|u[ -]+shaped)[ -]+line|"
    r"dalai[ -]+lama|one[ -]+country[ -]+two[ -]+systems|"
    r"кита[йяею]|китайск\w*|тайван\w*|гонконг\w*"
    r")(?![^\W\d_])"
)
_OTHER_SCRIPTS = re.compile(r"الصين|تايوان|هونغ كونغ|التبت|شينجيانغ|대만|중국|홍콩|티베트|"
                            r"タイワン|チベット|シンチヤン|"
                            r"trung quoc|đai loan|hong kong|tay tang|tan cuong")


def strict_scope(rules):
    return rules.get('publication_scope') == 'academic_metadata'


def approved_source(source):
    issn = APPROVED_JOURNALS.get(source['id'])
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
                and APPROVED_JOURNALS[source['id']] in (bibliography.get('ISSN') or ())
                and bibliography.get('container-title'))


def snapshot_issues(data, sources, rules):
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


def source_paused(source, rules):
    return (source['id'] in rules.get('paused_sources', ())
            or source['id'] in rules.get('hidden_sources', ())
            or source['module'] in rules.get('paused_modules', ())
            or (strict_scope(rules) and not approved_source(source)))


def _normalized(value):
    value = html.unescape(value)
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value: break
        value = decoded
    value = unicodedata.normalize('NFKC', value).casefold()
    value = ''.join(character for character in unicodedata.normalize('NFKD', value)
                    if unicodedata.category(character) not in {'Mn', 'Cf'})
    return re.sub(r'[\s_‐‑‒–—−·/]+', ' ', value)


def restricted_content(value, rules):
    """Inspect full accessible text and nested metadata, independently of presentation."""
    if not (strict_scope(rules) or rules.get('screen_china_related_materials')):
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
