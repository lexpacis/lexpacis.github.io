"""Extract explicit calendar days from primary announcements, without a model."""
import datetime as dt
import json
import re

from core import parse_date


MONTH = (r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
         r'Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?')
DAY = rf'(?:20\d{{2}}-\d{{2}}-\d{{2}}|20\d{{2}}年\s*\d{{1,2}}月\s*\d{{1,2}}日|\d{{1,2}}\s+(?:{MONTH})\.?\s+20\d{{2}}|(?:{MONTH})\.?\s+\d{{1,2}},?\s+20\d{{2}})'
SPAN = re.compile(rf'(?<!\d)(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+({MONTH})\.?\s+(20\d{{2}})(?!\d)',re.I)
MONTH_FIRST_SPAN = re.compile(rf'({MONTH})\.?\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}}),?\s+(20\d{{2}})',re.I)
EXACT_DAY = re.compile(rf'(?<![\w/]){DAY}(?![\w/])',re.I)
SUBMISSION = re.compile(r'call for (?:papers|abstracts|contributions|submissions)\b|\bCfP\b|征稿|征文',re.I)
CANCELLED = re.compile(r'\b(?:event|conference|webinar|workshop)\s+(?:has been |is )?(?:cancelled|canceled|postponed)\b|活动取消|会议取消|活动延期|会议延期',re.I)
CONFIRMED_LABELS = {'活动日期','摘要提交截止','投稿截止'}


def day(value):
    value=re.sub(r'\s+',' ',value.strip().replace('.',''))
    iso=value[:10] if re.fullmatch(r'20\d{2}-\d{2}-\d{2}T[^\s]+',value) else value
    parsed,precision=parse_date(iso)
    if precision=='day':
        return parsed
    for fmt in ('%b %d %Y','%b %d, %Y'):
        try:
            return dt.datetime.strptime(value,fmt).date().isoformat()
        except ValueError:
            pass
    return None


def date_value(date,label,end=None):
    value={'date':date,'date_text':date,'label':label}
    if end and end!=date:
        value['end_date']=end
    return value


def clean_date(value, labels=CONFIRMED_LABELS):
    if (not isinstance(value,dict) or not {'date','date_text','label'}<=value.keys()
        or set(value)-{'date','date_text','label','end_date'} or not isinstance(value['label'],str)
        or value['label'] not in labels):
        return None
    date,end=value['date'],value.get('end_date')
    if not isinstance(date,str) or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',date) or day(date)!=date or value['date_text']!=date:
        return None
    if end is not None:
        if (value['label']!='活动日期' or not isinstance(end,str) or day(end)!=end
            or not 0 <= (dt.date.fromisoformat(end)-dt.date.fromisoformat(date)).days<=14):
            return None
    return date_value(date,value['label'],end)


def event_date(body, structured=(), visible=()):
    if CANCELLED.search(body):
        return None
    if any(str(event.get('eventStatus','')).rsplit('/',1)[-1] in {'EventCancelled','EventPostponed','EventRescheduled'}
           for event in structured):
        return None
    result=set()
    for event in structured:
        start=day(event['startDate']) if isinstance(event.get('startDate'),str) else None
        end=day(event['endDate']) if isinstance(event.get('endDate'),str) else start
        if start and end:
            result.add((start,end))
    text_dates=set()
    for value in visible:
        span=SPAN.search(value)
        if span:
            first,last,month,year=span.groups()
            date,end=day(f'{first} {month} {year}'),day(f'{last} {month} {year}')
            if date and end:
                text_dates.add((date,end))
        elif span:=MONTH_FIRST_SPAN.search(value):
            month,first,last,year=span.groups()
            date,end=day(f'{first} {month} {year}'),day(f'{last} {month} {year}')
            if date and end:
                text_dates.add((date,end))
        else:
            for match in EXACT_DAY.finditer(value):
                date=day(match[0])
                if date:
                    text_dates.add((date,date))
    if not result:
        labels=(r'\b(?:event dates?|conference dates?|workshop dates?|date and venue)\s*[:：]?\s*'
                r'|\b(?:conference|workshop|forum|webinar)[^.;]{0,125}?\b(?:from|held on|take place on)\s+'
                r'|(?:活动时间|会议日期|活动日期)\s*[:：]\s*')
        for match in re.finditer(labels,body,re.I):
            following=body[match.end():match.end()+70]
            span=SPAN.match(following)
            one=EXACT_DAY.match(following)
            if span:
                first,last,month,year=span.groups()
                start,end=day(f'{first} {month} {year}'),day(f'{last} {month} {year}')
                if start and end:
                    text_dates.add((start,end))
            elif one and (date:=day(one[0])):
                text_dates.add((date,date))
    if result and text_dates and {x[0] for x in result}!={x[0] for x in text_dates}:
        return None
    candidates=result or text_dates
    if len(candidates)==1:
        start,end=candidates.pop()
        return clean_date(date_value(start,'活动日期',end))
    return None


def structured_events(markup,title):
    events=[]
    title_key=' '.join(title.casefold().split())
    for match in re.finditer(r'<script\b[^>]*\btype\s*=\s*["\x27]application/ld\+json["\x27][^>]*>(.*?)</script\s*>',
                             markup,re.I|re.S):
        if len(match[1])>24000:
            continue
        try:
            raw=json.loads(match[1])
        except (ValueError,RecursionError):
            continue
        nodes=raw.get('@graph',[raw]) if isinstance(raw,dict) else raw
        if not isinstance(nodes,list):
            continue
        for node in nodes:
            if (isinstance(node,dict) and node.get('@type') in ('Event','https://schema.org/Event')
                and isinstance(node.get('name'),str) and ' '.join(node['name'].casefold().split())==title_key):
                events.append(node)
    return events


def submission_deadline(title,body):
    if not SUBMISSION.search(title+' '+body):
        return None
    abstract=(r'(?:abstract|proposal)s?\s+submission\s+deadline|(?:abstract|proposal)s?\s+must\s+be\s+submitted\s+by|'
              r'(?:deadline\s+for\s+)?submission\s+of\s+(?:an?\s+(?:\d{2,4}[- ]word\s+)?)?(?:abstract|proposal)s?(?:\s*\([^)]{1,70}\)|\s+for either articles or case notes)?|'
              r'(?:abstract|proposal)s?\s+(?:are\s+)?due|(?:摘要|提案)(?:提交|投稿)?截止(?:日期|时间)?')
    general=(r'submissions?\s+deadline|deadline\s+for\s+submissions|'
             r'submissions?\s+(?:should|must)\s+be\s+(?:emailed|sent)\s+to\s+[\w.+-]+@[\w.-]+\s+by(?:\s+no\s+later\s+than)?|'
             r'submissions?\s+close(?:s)?|投稿截止(?:日期|时间)?|截稿(?:日期|时间)?')
    for labels,label in ((abstract,'摘要提交截止'),(general,'投稿截止')):
        pattern=re.compile(rf'(?:{labels})\s*(?::|：|is\b|on\b|by\b)?\s*(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?({DAY})',re.I)
        dates=set()
        for match in pattern.finditer(body):
            qualifier=body[max(0,match.start()-28):match.start()]
            if label=='投稿截止' and re.search(r'\b(?:final|full|draft|accepted)(?:\s+papers?)?\s*$',qualifier,re.I):
                continue
            if parsed:=day(match[1]):
                dates.add(parsed)
        if dates:
            return date_value(dates.pop(),label) if len(dates)==1 else None
    return None


def item_notices(item):
    facts=item.get('bibliography',{}).get('notice_dates')
    if not isinstance(facts,dict):
        return None,None
    event=clean_date(facts.get('event'),{'活动日期'}) if item.get('module')=='events' else None
    deadline=clean_date(facts.get('deadline'),{'投稿截止','摘要提交截止'}) if item.get('module') in ('events','cfp') else None
    return event,deadline
