import copy
from contextlib import closing
import json
from email.message import Message
from io import BytesIO
from pathlib import Path
import socket
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import core,cfp,pipeline,rendering,publishing,manage,network_policy

class NetworkPolicyTests(unittest.TestCase):
    def result(self,address):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
    def test_public_approved_host(self):
        with patch.object(network_policy.socket,'getaddrinfo',return_value=self.result('8.8.8.8')):
            self.assertEqual(network_policy.validate_public_url('https://journal.example/path',{'journal.example'}),'https://journal.example/path')
    def test_private_and_reserved_destinations_rejected(self):
        for address in ('127.0.0.1','192.168.1.1','169.254.169.254','198.18.0.1','0.0.0.0'):
            with self.subTest(address=address),patch.object(network_policy.socket,'getaddrinfo',return_value=self.result(address)):
                with self.assertRaises(ValueError):network_policy.validate_public_url('https://journal.example/',{'journal.example'})
    def test_unknown_host_credentials_and_ports_rejected(self):
        for url in ('https://evil.example/','https://name:secret@journal.example/','https://journal.example:8000/','file:///etc/passwd'):
            with self.subTest(url=url),self.assertRaises(ValueError):network_policy.validate_public_url(url,{'journal.example'})
    def test_https_downgrade_rejected(self):
        request=urllib.request.Request('https://journal.example/')
        with self.assertRaises(ValueError):network_policy.RestrictedRedirect({'journal.example'}).redirect_request(request,None,302,'',{},'http://journal.example/')
    def test_connection_uses_checked_socket_address(self):
        addresses=self.result('8.8.8.8')
        with patch.object(network_policy.socket,'getaddrinfo',return_value=addresses) as resolve:
            with patch.object(network_policy.socket,'socket') as open_socket:
                link=network_policy._connect_public(('journal.example',443),7,None)
                link.connect.assert_called_once_with(('8.8.8.8',443))
                open_socket.assert_called_once_with(socket.AF_INET,socket.SOCK_STREAM,6)
            resolve.assert_called_once()
    def test_collectors_reject_plaintext_before_dns(self):
        with patch.object(network_policy.socket,'getaddrinfo') as resolve:
            with self.assertRaises(ValueError):
                network_policy.public_opener({'journal.example'}).open('http://journal.example/')
            resolve.assert_not_called()

class FeatureWorkflowTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'runtime/test-temp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='features-',dir=parent);self.root=Path(self.temp.name)
        self.patches=[patch.object(module,'ROOT',self.root) for module in (core,cfp,pipeline,rendering,publishing,manage)]
        for p in self.patches:p.start()
        import shutil
        shutil.copytree(ROOT/'web',self.root/'web');shutil.copytree(ROOT/'config',self.root/'config')
        self.config=core.read_json(self.root/'config/site.json');self.conn=core.database(self.root)
        self.evidence='Call for papers on international law. Abstracts are due by 10 January 2027.'
        core.write_json(self.root/'runtime/cfp/state.json',{'pages':{'journal:ljil':{'id':'journal:ljil','url':'https://www.cambridge.org/test-call','status':'identity_matched','content_hash':'current','evidence_excerpt':self.evidence}}})
        self.call={'id':'cfp-test-call','title':'国际法专题征稿','original_title':'Call for papers on international law','kind':'journal_special','journal_id':'ljil','language':'en',
                   'url':'https://www.cambridge.org/test-call','description':'征集国际法论文摘要。','deadline':'2027-01-10','deadline_text':'10 January 2027','deadline_label':'摘要截止','deadline_timezone':None,'event_date':None,
                   'topics':['theory'],'page_id':'journal:ljil','content_hash':'current','evidence_quote':'Abstracts are due by 10 January 2027.'}
    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def apply(self,call):
        path=self.root/'runtime/cfp/output.json';core.write_json(path,{'model':'fixture-only','calls':[call]});cfp.apply_updates(path)
    def test_valid_cfp_update(self):
        self.apply(self.call)
        self.assertEqual(core.read_json(self.root/'runtime/cfp/accepted-calls.json')[0]['deadline'],'2027-01-10')
    def test_cfp_invented_deadline_rejected(self):
        self.call['deadline']='2027-01-11'
        with self.assertRaises(ValueError):self.apply(self.call)
    def test_cfp_unknown_action_or_unread_link_rejected(self):
        self.call['command']='run unknown program'
        with self.assertRaises(ValueError):self.apply(self.call)
        self.call.pop('command');self.call['url']='https://example.org/unread'
        with self.assertRaises(ValueError):self.apply(self.call)
    def test_cfp_stale_evidence_rejected(self):
        self.call['content_hash']='old'
        with self.assertRaises(ValueError):self.apply(self.call)
    def test_cfp_retraction_in_calendar_and_json(self):
        core.write_json(self.root/'config/visibility.json',{'hidden_items':['cfp-ljil-law-society-2027']})
        data=pipeline.public_data(self.conn,self.config,core.read_json(self.root/'config/sources.json'))
        rendering.write_release(data)
        self.assertNotIn('cfp-ljil-law-society-2027',(self.root/'dist/cfp.ics').read_text(encoding='utf-8'))
        self.assertNotIn('cfp-ljil-law-society-2027',(self.root/'dist/api/cfp.json').read_text(encoding='utf-8'))
    def test_editorial_policy_covers_all_public_exports(self):
        core.write_json(self.root/'config/visibility.json',{'paused_modules':['news'],'paused_sources':['wto'],
                        'screen_china_related_materials':True})
        sources=core.read_json(self.root/'config/sources.json')
        by_id={source['id']:source for source in sources}
        entries=[
            ('keep','Award interpretation under investment treaties','','crossref-ejil'),
            ('title','A comparative study of Chinese arbitration','','crossref-ejil'),
            ('evidence','A public law case study','The original discusses Taiwan.','crossref-ejil'),
            ('translation','Approaches to arbitration disclosure','','crossref-ejil'),
            ('provenance','Treaty access in archives','','crossref-ejil'),
            ('news','New research library opens','','un-geneva-news'),
            ('wto','A neutral institutional alert','','wto')
        ]
        for slug,title,evidence,source_id in entries:
            url='https://example.org/'+slug
            core.upsert_item(self.conn,{'title':title,'url':url,'evidence':evidence},by_id[source_id],self.config)
        restricted_ids={core.identity('https://example.org/'+entry[0]) for entry in entries[1:]}
        translated_id=core.identity('https://example.org/translation')
        translated=self.conn.execute('SELECT * FROM items WHERE id=?',(translated_id,)).fetchone()
        enrichment={'title_zh':'中国仲裁披露规则','summary_zh':'','module':'research','topics':[],'event':None,'deadline':None}
        self.conn.execute('INSERT INTO enrichments VALUES(?,?,?,?,?)',
                          (translated_id,translated['content_hash'],json.dumps(enrichment),'fixture',core.now()))
        self.conn.execute('INSERT INTO provenance VALUES(?,?,?,?)',
                          (core.identity('https://example.org/provenance'),'crossref-ajil','https://example.org/related/%43hina',core.now()))
        self.call['title']='中国比较法专题征稿'
        self.apply(self.call)
        data=pipeline.public_data(self.conn,self.config,sources)
        self.assertEqual([item['id'] for item in data['items']],[core.identity('https://example.org/keep')])
        self.assertNotIn(self.call['id'],[call['id'] for call in data['cfp']['calls']])
        self.assertEqual({s['id'] for s in data['sources'] if s['status']=='paused'},
                         {'wto','un-geneva-news','un-geneva-press','un-geneva-meetings','un-news-global','china-mfa-statements','china-xinhua-politics'})
        rendering.write_release(data)
        for relative in ('data.js','api/items.json','feed.xml','feeds/modules/news.xml','sitemap.xml','calendar.ics','cfp.ics','api/cfp.json'):
            payload=(self.root/'dist'/relative).read_text(encoding='utf-8')
            for item_id in restricted_ids|{self.call['id']}:
                self.assertNotIn(item_id,payload)
        for item_id in restricted_ids:
            self.assertFalse((self.root/'dist/items'/f'{item_id}.html').exists())
    def test_strict_scope_requires_context_and_recognizes_regional_names(self):
        import cloud
        from publication_policy import snapshot_issues
        core.write_json(self.root/'config/visibility.json',{'publication_scope':'academic_metadata'})
        sources=core.read_json(self.root/'config/sources.json')
        source=next(s for s in sources if s['id']=='crossref-ejil')
        acknowledged={'title':'Methods of interpreting treaty reservations','doi':'10.1234/article',
                      'url':'https://doi.org/10.1234/article','authors':['A. Scholar'],'published_at':'2026-02-01',
                      'evidence':'The article assesses interpretive methods used to evaluate the effects of multilateral treaty reservations.',
                      'bibliography':{'type':'journal-article','ISSN':['0938-5428'],'container-title':['Academic journal']}}
        cases=[
            ('allowed',source,{}),
            ('no-abstract',source,{'evidence':''}),
            ('insufficient-context',source,{'evidence':'Abstract'}),
            ('unlisted-context',source,{'evidence':acknowledged['evidence']+' The case concerns Kowloon.'}),
            ('alternate-place-name',source,{'title':'Arbitration and public institutions in Lhasa'}),
            ('maritime-reference',source,{'title':'Contemporary approaches to the 9-dash line'}),
            ('traditional',source,{'title':'金門公法研究'}),
            ('non-journal',source,{'bibliography':{'type':'report','ISSN':['0938-5428'],'container-title':['Academic journal']}}),
            ('blog',next(s for s in sources if s['id']=='ejiltalk'),{}),
            ('news',next(s for s in sources if s['id']=='un-geneva-press'),{}),
            ('institution',next(s for s in sources if s['id']=='icj'),{}),
        ]
        for name,origin,overrides in cases:
            doi='10.1234/'+name
            core.upsert_item(self.conn,{**acknowledged,'doi':doi,'url':'https://doi.org/'+doi,**overrides},origin,self.config)
        data=pipeline.build(self.conn,self.config,sources)
        self.assertEqual([item['doi'] for item in data['items']],['10.1234/allowed'])
        self.assertFalse(data['cfp']['calls'])
        self.assertFalse(snapshot_issues(data,sources,pipeline.publication_rules()))
        self.assertNotIn(acknowledged['evidence'],(self.root/'dist/data.js').read_text(encoding='utf-8'))
        for path in ('feed.xml','sitemap.xml','api/items.json','api/cfp.json','calendar.ics','cfp.ics'):
            exported=(self.root/'dist'/path).read_text(encoding='utf-8')
            self.assertFalse(any('10.1234/'+name in exported for name,_,_ in cases[1:]))
        with closing(core.database(self.root/'next-run')) as next_run:
            cloud.restore_snapshot(next_run,data,self.config,sources)
            self.assertEqual(len(pipeline.public_data(next_run,self.config,sources)['items']),1)
            next_run.execute("UPDATE items SET content_hash=? WHERE id=?",('a'*64,data['items'][0]['id']))
            self.assertEqual(pipeline.public_data(next_run,self.config,sources)['items'],[])
    def test_strict_collection_never_requests_unapproved_sources(self):
        sources=core.read_json(self.root/'config/sources.json')
        core.write_json(self.root/'config/visibility.json',{'publication_scope':'academic_metadata'})
        called=[]
        def fetch(source,config,state):
            called.append(source['id'])
            return b'{"message":{"items":[]}}',{}
        with patch.object(pipeline,'http_fetch',side_effect=fetch):
            pipeline.collect(self.conn,self.config,sources)
        self.assertEqual(called,['crossref-ejil','crossref-ajil','crossref-ljil'])
        rules=pipeline.publication_rules()
        rules['automatic_updates_paused']=True
        core.write_json(self.root/'config/visibility.json',rules)
        with patch.object(pipeline,'http_fetch',side_effect=AssertionError('automatic fetch must stay paused')):
            pipeline.collect(self.conn,self.config,sources,force=True)
    def test_regional_origin_checks_whole_context_and_original_publisher(self):
        from source_review import inspect_html, verify_entries
        from publication_policy import snapshot_issues
        config,sources=pipeline.configuration()
        rules=pipeline.publication_rules()
        mfa=next(s for s in sources if s['id']=='china-mfa-statements')
        foreign=next(s for s in sources if s['id']=='un-geneva-press')
        body='A public report on procedural reforms of a regional institution and upcoming events affecting its jurisdiction. '*3
        def record(url,title):
            return {'url':url,'title':title,'evidence_basis':'RSS 标题','evidence':'','authors':[]}
        foreign_item=record('https://www.ungeneva.org/example-foreign','A regional institution updates procedural rules')
        other_item=record('https://www.ungeneva.org/example-other','A second institution updates procedural rules')
        rss_item=record('https://www.ungeneva.org/example-rss','A third institution updates procedural rules')
        rss_item.update(evidence=body,evidence_basis='RSS 正文')
        origin=record('https://www.mfa.gov.cn/fyrbt_673021/dhdw_673027/202610/t20261010_123.shtml','外交部发言人就西藏问题答记者问')
        repost=record('https://www.mfa.gov.cn/fyrbt_673021/dhdw_673027/202610/t20261010_456.shtml','外交部发言人就藏南问题答记者问')
        repost_header={**repost,'url':repost['url'].replace('_456','_457')}
        missing_title={**repost,'url':repost['url'].replace('_456','_458')}
        mfa_meta='<meta name="SiteName" content="中华人民共和国外交部">'
        with patch('source_review.public_opener',side_effect=OSError) as validate:
            # A blocked full text no longer discards the record: the official title and
            # link stay usable, so it is published at title level after screening.
            published=verify_entries([rss_item],foreign,config,rules)
            self.assertEqual(len(published),1)
            self.assertEqual(published[0]['evidence_basis'],'RSS 标题')
            blocked={**rss_item,'title':'A third institution updates its rules on Tibet',
                     'url':'https://www.ungeneva.org/example-blocked'}
            held={}
            verify_entries([blocked],foreign,config,rules,held)
            self.assertEqual(held['china_hold'],1)
            self.assertEqual(held['title_fallback'],0)
            validate.assert_called()
        core.upsert_item(self.conn,rss_item,foreign,config)
        for source,entry,markup in (
            (foreign,foreign_item,f'<article>{body}</article>'),
            (foreign,other_item,f'<meta property="og:description" content="A case concerns Arunachal."><article>{body}</article>'),
            (mfa,origin,f'{mfa_meta}<h1>{origin["title"]}</h1><article>{body}</article>'),
            (mfa,repost,f'{mfa_meta}<meta name="ContentSource" content="外交部"><meta name="ContentSource" content="Third-party wire"><h1>{repost["title"]}</h1><article>{body}</article>'),
            (mfa,repost_header,f'{mfa_meta}<header><h1>{repost["title"]}</h1><p>来源：<a>其他通讯社</a></p></header><article>{body}</article>'),
            (mfa,missing_title,f'{mfa_meta}<article>{body}</article>'),
            (foreign,record('https://www.ungeneva.org/no-context','Context cannot be established'),'<header>Preview</header>'),
        ):
            core.upsert_item(self.conn,inspect_html(entry,source,entry['url'],markup),source,config)
        release=pipeline.build(self.conn,config,sources)
        # A record whose body carries no usable context is still publishable at title
        # level; the item page states that the full text was not checked.
        self.assertEqual({i['url'] for i in release['items']},
                         {foreign_item['url'],origin['url'],'https://www.ungeneva.org/no-context'})
        self.assertFalse(snapshot_issues(release,sources,rules))
        self.assertNotIn(other_item['url'],(self.root/'dist/feed.xml').read_text(encoding='utf-8'))
        self.assertNotIn(repost['url'],(self.root/'dist/api/items.json').read_text(encoding='utf-8'))
    def test_emergency_pause_also_skips_cfp_checks(self):
        rules=pipeline.publication_rules()
        rules['automatic_updates_paused']=True
        core.write_json(self.root/'config/visibility.json',rules)
        with patch.object(cfp,'approved_targets',side_effect=AssertionError('CFP must stay paused')):
            self.assertEqual(cfp.refresh(self.config,force=True)['checked_this_run'],0)
    def test_source_specific_body_and_rotating_review(self):
        from source_review import verify_entries
        _,sources=pipeline.configuration()
        source=next(s for s in sources if s['id']=='wto')
        source={**source,'article_checks':2}
        body='The institution explained its programme for resolving international disputes and reviewing proposals. '*3
        entries=[{'title':f'Procedural update number {index}','url':f'https://www.wto.org/item-{index}',
                  'authors':[],'evidence':'','evidence_basis':'RSS 标题'} for index in range(5)]
        requests=[]

        def open_article(request,**kwargs):
            requests.append(request.full_url)
            response=BytesIO(f'<main id="mainContent"><h1>Official release</h1><p>{body}</p></main>'.encode())
            response.headers=Message()
            response.headers['Content-Type']='text/html; charset=UTF-8'
            response.url=request.full_url
            return response

        report={}
        rules=pipeline.publication_rules()
        with patch('source_review.public_opener',return_value=SimpleNamespace(open=open_article)):
            first=verify_entries(entries,source,self.config,rules,report)
            second=verify_entries(entries,source,self.config,rules,report)
        self.assertEqual(requests,[entries[i]['url'] for i in (0,1,0,2)])
        self.assertEqual(report['verified'],2)
        self.assertEqual(report['pending'],3)
        self.assertEqual(report['title_fallback'],3)
        # Entries outside the per-run fetch budget are published at title level instead
        # of being dropped, and keep their own basis.
        self.assertEqual(len(first),5)
        self.assertEqual(len(second),5)
        self.assertEqual(sum(1 for x in first+second if x['evidence_basis']=='原文正文核对'),4)
        self.assertEqual(sum(1 for x in first+second if x['evidence_basis']=='RSS 标题'),6)

    def test_icj_pdf_requires_all_pages_and_applies_editorial_screen(self):
        from publication_policy import regional_item
        from source_review import inspect_official_pdf, PDF_BASIS
        _,sources=pipeline.configuration()
        source=next(s for s in sources if s['id']=='icj')
        url='https://www.icj-cij.org/sites/default/files/case-related/178/178-20261103-pre-01-00-en.pdf'
        entry={'title':'Procedural order','url':url,'authors':[],'bibliography':{},'module':'cases','evidence':''}
        page='International Court of Justice. The judges explained the procedure for the parties and set further deadlines. '*2
        def mock_reader(args,**kwargs):
            if 'pdfinfo' in args:
                return SimpleNamespace(stdout=b'Pages: 2\n')
            kwargs['stdout'].write((page+'\f'+mock_reader.last_page+'\f').encode())
            return SimpleNamespace()
        mock_reader.last_page=page
        with patch('source_review.subprocess.run',side_effect=mock_reader):
            checked=inspect_official_pdf(entry,source,url,b'%PDF-fixture')
            self.assertEqual(checked['evidence_basis'],PDF_BASIS)
            rules=pipeline.publication_rules()
            self.assertTrue(regional_item(checked,source,rules,False))
            mock_reader.last_page=page+' The proceedings concern Lhasa.'
            self.assertFalse(regional_item(inspect_official_pdf(entry,source,url,b'%PDF-fixture'),source,rules,False))
            mock_reader.last_page=''
            self.assertIs(inspect_official_pdf(entry,source,url,b'%PDF-fixture'),entry)
            self.assertIs(inspect_official_pdf(entry,source,url.replace('icj-cij.org','example.org'),b'%PDF-fixture'),entry)

    def test_case_pages_require_source_specific_body_and_matching_pca_heading(self):
        from source_review import inspect_html
        _,sources=pipeline.configuration()
        body='The Permanent Court of Arbitration described the procedure followed by the tribunal and explained the decisions recorded in the case file. '*3
        pca=next(s for s in sources if s['id']=='pca')
        entry={'title':'Arbitral proceedings update','url':'https://pca-cpa.org/en/news/procedure-update/','evidence':''}
        markup=f'<h1>{entry["title"]}</h1><div><p>{body}</p></div>'
        checked=inspect_html(entry,pca,entry['url'],markup)
        self.assertEqual(checked['evidence_basis'],'原文正文核对')
        self.assertIs(inspect_html(entry,pca,entry['url'],markup.replace(entry['title'],'Page not found')),entry)
        icj=next(s for s in sources if s['id']=='icj-pending')
        record={'title':'Pending proceedings','url':'https://www.icj-cij.org/case/178'}
        markup=f'<h1>Pending proceedings</h1><div id="block-icj-content"><p>{body}</p></div>'
        self.assertEqual(inspect_html(record,icj,record['url'],markup)['evidence_basis'],'原文正文核对')

    def test_dates_preserve_original_calendar_range_and_submission_stage(self):
        from notice_dates import event_date, submission_deadline
        from rendering import calendar
        event=event_date('Conference dates: 9–11 September 2027. Published 10 March 2026.')
        self.assertEqual((event['date'],event['end_date']),('2027-09-09','2027-09-11'))
        self.assertIsNone(event_date('Conference dates: March 2027. Published 10 March 2026.'))
        self.assertIsNone(event_date('The workshop has been postponed. Event date: 7 December 2026.'))
        text=('Call for Papers. Abstract submission deadline: 8 December 2026. '
              'Full paper submission deadline: 15 July 2027. Attendance registration closes 8 January 2027.')
        deadline=submission_deadline('Call for Papers',text)
        self.assertEqual((deadline['date'],deadline['label']),('2026-12-08','摘要提交截止'))
        self.assertIsNone(submission_deadline('Call for Papers','Full paper submission deadline: 15 July 2027.'))
        self.assertIsNone(submission_deadline('Call for Papers','Abstracts due 8 December 2026. Abstracts due 9 December 2026.'))
        data={'site':{'name':'Lexpacis'},'generated_at':'2026-11-19T00:00:00+00:00',
              'items':[{'id':'fixture','title':'Conference','url':'https://example.org/','event':event,'ai':None}]}
        ics=calendar(data)
        self.assertIn('DTSTART;VALUE=DATE:20270909',ics)
        self.assertIn('DTEND;VALUE=DATE:20270912',ics)

    def test_verified_cfp_requires_original_notice_and_agrees_with_parent(self):
        from publication_policy import snapshot_issues
        from source_review import inspect_html
        config,sources=pipeline.configuration()
        source=next(s for s in sources if s['id']=='cambridge-cfp-ljil')
        entry={'title':'Special issue on international institutions','url':source['url']+'/fixture',
               'authors':[],'published_at':None,'evidence':''}
        fragment='Call for Papers. Research on international institutions is invited. '*4
        markup=f'<h1>{entry["title"]}</h1><div id="MAIN_CONTENT">{fragment}Submission of Abstracts: 10 January 2027.</div>'
        checked=inspect_html(entry,source,entry['url'],markup)
        core.upsert_item(self.conn,checked,source,config)
        public=pipeline.public_data(self.conn,config,sources)
        row=public['items'][0]
        self.assertEqual(row['module'],'cfp')
        self.assertEqual(public['cfp']['calls'][0]['deadline'],'2027-01-10')
        self.assertEqual(public['cfp']['calls'][0]['verification'],'primary_notice')
        self.assertFalse(snapshot_issues(public,sources,pipeline.publication_rules()))
        public['cfp']['calls'][0]['deadline']='2027-01-11'
        self.assertTrue(snapshot_issues(public,sources,pipeline.publication_rules()))
    def test_output_path_restricted(self):
        path=self.root/'external-output.json';core.write_json(path,{'model':'fixture-only','calls':[self.call]})
        with self.assertRaises(ValueError):cfp.apply_updates(path)
    def test_rename_is_central_and_keeps_url(self):
        before=self.config['base_url'];manage.rename('国际法学术观察站','JurisScope','连接原始资料与学术研究。')
        config=core.read_json(self.root/'config/site.json');self.assertEqual(config['base_url'],before)
        data=pipeline.public_data(self.conn,config,core.read_json(self.root/'config/sources.json'));rendering.write_release(data)
        self.assertIn('国际法学术观察站',(self.root/'dist/index.html').read_text(encoding='utf-8'))
        self.assertNotIn('法域前沿',(self.root/'dist/index.html').read_text(encoding='utf-8'))
        self.assertIn('国际法学术观察站',(self.root/'dist/calendar.ics').read_text(encoding='utf-8'))
        self.assertIn('JurisScope',(self.root/'dist/data.js').read_text(encoding='utf-8'))
    def test_rename_rejects_html(self):
        with self.assertRaises(ValueError):manage.rename('<script>alert(1)</script>')
    def test_cfp_directory_scope(self):
        public=cfp.public_data(self.conn,[])
        self.assertEqual(len(public['journals']),41)
        self.assertEqual(sum(j['language'] in ('zh','bilingual') for j in public['journals']),14)
        self.assertTrue(all(j.get('submissions_url') for j in public['journals']))
    def test_chinese_rendering_publishes_beside_the_original(self):
        from publication_policy import snapshot_issues
        _,sources=pipeline.configuration();rules=pipeline.publication_rules()
        source=next(s for s in sources if s['id']=='wto')
        url='https://www.wto.org/english/news_e/news26_e/report_e.htm'
        core.upsert_item(self.conn,{'title':'Panel report on technical barriers to trade','url':url,
                                    'evidence':'','evidence_basis':'官方目录标题','authors':[]},source,self.config)
        key=core.identity(url)
        revision=self.conn.execute('SELECT content_hash FROM items WHERE id=?',(key,)).fetchone()[0]
        payload={'id':key,'content_hash':revision,'title_zh':'技术性贸易壁垒专家组报告','summary_zh':'',
                 'topics':['economic'],'module':'official','evidence_quotes':[],'event':None,'deadline':None}
        self.conn.execute('INSERT OR REPLACE INTO enrichments VALUES(?,?,?,?,?)',
                          (key,revision,json.dumps(payload,ensure_ascii=False),'fixture',core.now()))
        self.conn.commit()
        release=pipeline.build(self.conn,self.config,sources)
        self.assertEqual([i['title_zh'] for i in release['items']],['技术性贸易壁垒专家组报告'])
        page=(self.root/'dist/items'/(key+'.html')).read_text(encoding='utf-8')
        self.assertIn('技术性贸易壁垒专家组报告',page)
        self.assertIn('Panel report on technical barriers to trade',page)
        self.assertFalse(snapshot_issues(release,sources,rules))
        # A translation is screened like the source text: it cannot carry wording
        # that the original-language rule would have held back.
        payload['title_zh']='关于中国稀土出口管制的专家组报告'
        self.conn.execute('UPDATE enrichments SET payload=? WHERE item_id=?',(json.dumps(payload,ensure_ascii=False),key))
        self.conn.commit()
        self.assertEqual(pipeline.build(self.conn,self.config,sources)['items'],[])
    def test_dated_records_sort_before_undated_ones(self):
        _,sources=pipeline.configuration()
        source=next(s for s in sources if s['id']=='wto')
        for title,url,published,precision in (
            ('Undated notice','https://www.wto.org/english/news_e/news26_e/undated_e.htm',None,'unknown'),
            ('Dated report','https://www.wto.org/english/news_e/news20_e/dated_e.htm','2020-01-02','day')):
            core.upsert_item(self.conn,{'title':title,'url':url,'published_at':published,
                                        'date_precision':precision,'evidence':'','evidence_basis':'官方目录标题',
                                        'authors':[]},source,self.config)
        self.conn.commit()
        rows=pipeline.public_data(self.conn,self.config,sources)['items']
        self.assertEqual([row['title'] for row in rows],['Dated report','Undated notice'])
    def test_case_page_date_is_taken_from_the_docket_and_labelled(self):
        from source_review import inspect_html
        _,sources=pipeline.configuration()
        body='The case concerns a dispute between two states over the interpretation of a treaty. '*3
        icj=next(s for s in sources if s['id']=='icj-pending')
        icj_url='https://www.icj-cij.org/case/999'
        icj_markup=('<div id="block-icj-content"><h1>Application of a convention</h1><article>'+body+'</article>'
                    '<h6 class="date">\n  12 \n  November \n  2024\n</h6>'
                    '<h6 class="date">17 December 2025</h6></div>')
        entry={'url':icj_url,'title':'Application of a convention','evidence':'','evidence_basis':'官方案件目录'}
        result=inspect_html(entry,icj,icj_url,icj_markup)
        self.assertEqual(result['published_at'],'2025-12-17')
        self.assertEqual(result['bibliography']['date_note'],'案件页最新文书日期')
        itlos=next(s for s in sources if s['id']=='itlos-docket')
        itlos_url='https://www.itlos.org/en/main/cases/list-of-cases/case-concerning-a-dispute/'
        itlos_markup=('<div class="fs-content-container"><h1>Case concerning a dispute</h1><article>'+body+'</article>'
                      '<p>Order 2026/6 of 24 June 2026</p><p>Order 2026/8 of 4 August 2026</p>'
                      '<p>A hearing will open on 2 July 2026</p></div>')
        result=inspect_html({**entry,'url':itlos_url},itlos,itlos_url,itlos_markup)
        self.assertEqual(result['published_at'],'2026-08-04')
        self.assertEqual(result['bibliography']['date_note'],'案件页最新文书日期')

if __name__=='__main__':unittest.main()
