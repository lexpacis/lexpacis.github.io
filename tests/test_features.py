import copy
from contextlib import closing
import json
from pathlib import Path
import socket
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
                         {'wto','un-geneva-news','un-geneva-press','un-geneva-meetings','china-mfa-statements','china-xinhua-politics'})
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
            self.assertFalse(verify_entries([rss_item],foreign,config,rules))
            validate.assert_called_once()
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
        self.assertEqual({i['url'] for i in release['items']},{foreign_item['url'],origin['url']})
        self.assertFalse(snapshot_issues(release,sources,rules))
        self.assertNotIn(other_item['url'],(self.root/'dist/feed.xml').read_text(encoding='utf-8'))
        self.assertNotIn(repost['url'],(self.root/'dist/api/items.json').read_text(encoding='utf-8'))
    def test_emergency_pause_also_skips_cfp_checks(self):
        rules=pipeline.publication_rules()
        rules['automatic_updates_paused']=True
        core.write_json(self.root/'config/visibility.json',rules)
        with patch.object(cfp,'approved_targets',side_effect=AssertionError('CFP must stay paused')):
            self.assertEqual(cfp.refresh(self.config,force=True)['checked_this_run'],0)
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
        self.assertEqual(len(public['journals']),36)
        self.assertEqual(sum(j['language'] in ('zh','bilingual') for j in public['journals']),14)
        self.assertTrue(all(j.get('submissions_url') for j in public['journals']))

if __name__=='__main__':unittest.main()
