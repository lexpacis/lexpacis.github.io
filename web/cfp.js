/* CFP publication board: directories, dated calls, and unverified leads remain distinct. */
(() => {
  'use strict';
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const url=v=>{try{const u=new URL(v);return u.protocol==='https:'&&!u.username&&!u.password?e(u.href):'';}catch{return '#';}};
  let host,source,topics,view='',language='',topic='',query='',dateFilter='upcoming';
  const kindName={journal_special:'期刊专题征稿',journal_notice:'常规征稿公告',conference:'会议征文',discovery:'待确认线索'};
  const date=v=>v?v.slice(0,10).replaceAll('-','.'):'';
  const today=()=>new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  const isPast=call=>Boolean(call.deadline&&call.deadline<today());
  const languages={zh:'中文',en:'英文',bilingual:'中英文'};
  const filtered=list=>list.filter(item=>(!language||item.language===language||(language==='zh'&&item.language==='bilingual'))&&(!topic||item.topics?.includes(topic))&&(!query||[item.name,item.title,item.original_title,item.scope,item.publisher,item.description].join(' ').toLowerCase().includes(query.toLowerCase())));
  const options=(list,value)=>list.map(([v,n])=>`<option value="${e(v)}" ${v===value?'selected':''}>${e(n)}</option>`).join('');
  const views=[['journal','期刊征稿'],['conference','会议征文'],['directory','投稿导航'],['discovery','待确认线索']];
  function render(data) {
    source=data.cfp||{calls:[],journals:[]};
    topics=data.topics;
    const directoryOnly=data.publication_scope==='academic_metadata';
    if (directoryOnly) view='directory';
    if (!view) {
      const upcoming=source.calls.filter(call=>call.deadline&&!isPast(call));
      view=upcoming.some(call=>['journal_special','journal_notice'].includes(call.kind))?'journal':
        upcoming.some(call=>call.kind==='conference')?'conference':'directory';
    }
    return `<section class="cfp-workbench">
      ${directoryOnly?'':`<div class="cfp-nav" role="group" aria-label="CFP 分类">
        ${views.map(([value,label])=>`<button type="button" data-cfp-view="${value}" aria-pressed="${view===value}" class="${view===value?'active':''}">${label}</button>`).join('')}
        <a class="text-button cfp-calendar-link" href="cfp.ics">截止日期 ICS ↗</a>
      </div>`}
      <div class="cfp-toolbar">
        <label class="search-box"><span aria-hidden="true">⌕</span>
          <input id="cfp-search" type="search" aria-label="搜索期刊和征稿" placeholder="搜索刊名、主题或机构" value="${e(query)}">
        </label>
        <select id="cfp-language" class="filter-select" aria-label="按征稿语言筛选">
          ${options([['','全部语言'],['zh','中文 / 中英文'],['en','英文'],['bilingual','中英文刊物']],language)}
        </select>
        <select id="cfp-topic" class="filter-select" aria-label="按研究方向筛选">
          ${options([['','全部方向'],...topics.map(topic=>[topic.id,topic.short])],topic)}
        </select>
        <select id="cfp-date" class="filter-select" aria-label="按截止日期筛选" ${view==='directory'?'hidden':''}>
          ${options([['upcoming','当前或未注明期限'],['all','含历史公告'],['dated','有明确截止日']],dateFilter)}
        </select>
      </div>
      <p id="cfp-count" class="cfp-results-count" role="status"></p>
      <div id="cfp-results"></div>
      <p class="cfp-footer"><a href="cfp-policy.html">收录与核对规则 ↗</a> · <a href="api/cfp.json">公开数据 ↗</a></p>
    </section>`;
  }
  function journalCard(j){
    const status={identity_matched:'最近读取页面包含刊名',reachable_unconfirmed:'页面可读，期刊身份待确认',error:'本次访问异常，未更新验证',unverified:'尚未运行入口检查',https_unverified:'尚无核实的 HTTPS 投稿入口'}[j.check_status]||'待确认';
    const reason=j.check_reason||'';
    const explanation=/^http_[45]\d\d$/.test(reason)?' · HTTP '+reason.slice(5):
      ({network_error:' · 连接或读取失败',response_invalid:' · 响应不符合入口检查要求'}[reason]||'');
    const origin=url(j.identity_source)||url(j.homepage);
    const submission=url(j.submissions_url);
    const originLink=origin?`<a href="${origin}" target="_blank" rel="noopener noreferrer">主办 / 出版机构来源 ↗</a>`:'';
    const submissionLink=submission?`<a href="${submission}" target="_blank" rel="noopener noreferrer">${j.check_status==='identity_matched'?'稿约 / 投稿入口':'查看登记入口（待核对）'} ↗</a>`:'<span class="tool-fine">待核对 HTTPS 投稿入口，请向主办机构确认。</span>';
    const labels=(j.topics||[]).map(id=>topics.find(t=>t.id===id)?.short).filter(Boolean).join(' / ');
    return `<article class="journal-card"><div class="journal-labels"><span>${e(languages[j.language]||j.language)}</span><span>${e(labels)}</span></div><h2>${e(j.name)}</h2><div class="journal-publisher">${e(j.publisher)}</div><div class="journal-scope">${e(j.scope)}</div>${j.note?`<p class="tool-fine" style="margin:0">${e(j.note)}</p>`:''}<div class="journal-state">${status}${e(explanation)}${j.checked_at?' · '+date(j.checked_at):''}${j.changed?' · 页面有变化，待更新稿约信息':''}<br>这是期刊入口，不表示当前有专题 CFP 或保证投稿窗口开放。</div><div class="journal-links">${originLink}${submissionLink}</div></article>`;
  }
  function callCard(call){
    const j=source.journals.find(j=>j.id===call.journal_id);
    const state=call.deadline?(isPast(call)?'已过公告截止日':'公告截止日未到'):'原文未注明固定截止日';
    return `<article class="cfp-call"><div class="cfp-call-top"><span class="cfp-kind">${kindName[call.kind]||'征稿公告'} · ${e(languages[call.language]||call.language)}</span><span class="cfp-state ${isPast(call)?'past':''}">${state}</span></div><h2>${e(call.title)}</h2>${call.original_title!==call.title?`<p class="cfp-original">${e(call.original_title)}</p>`:''}<p>${e(call.description)}</p><div class="cfp-deadline">${call.deadline?`${e(call.deadline_label||'截止日期')}：${date(call.deadline)}`:'未把发布日期或会议日期当作投稿截止日'}</div><div class="cfp-call-meta">${call.deadline_timezone?`原文时区：${e(call.deadline_timezone)}<br>`:call.deadline?'原文未给出精确时区；不提供精确到小时的倒计时。<br>':''}${e(j?.name||call.organization||'已接通信源')}<br>${call.verification==='discovered_title'?'尚未核对完整公告':'已核对原始公告'} · ${date(call.checked_at)}${call.source_changed?' · 来源页面有变化，请核对最新版':''}</div><div class="cfp-call-actions"><a class="text-button" href="${url(call.url)}" target="_blank" rel="noopener noreferrer">阅读原始公告 ↗</a>${call.kind==='conference'?'<span class="tool-fine" style="margin:0">参会不等于期刊录用</span>':''}</div></article>`;
  }
  function refresh() {
    const output=host.querySelector('#cfp-results');
    const count=host.querySelector('#cfp-count');
    if (view==='directory') {
      const list=filtered(source.journals);
      count.textContent=`找到 ${list.length} 种刊物`;
      output.innerHTML=`<div class="journal-grid">${list.length?list.map(journalCard).join(''):'<div class="cfp-grid-empty">没有匹配刊物。<button class="text-button" data-cfp-reset>清除筛选</button></div>'}</div>`;
      return;
    }
    let list=filtered(source.calls).filter(call=>view==='journal'?['journal_special','journal_notice'].includes(call.kind):call.kind===view);
    if (dateFilter==='upcoming') list=list.filter(call=>!isPast(call));
    if (dateFilter==='dated') list=list.filter(call=>call.deadline);
    list.sort((a,b)=> (a.deadline||'9999').localeCompare(b.deadline||'9999'));
    count.textContent=`找到 ${list.length} 条${view==='discovery'?'线索':'公告'}`;
    const empty=`<div class="cfp-grid-empty">当前筛选下没有收录资料。<button class="text-button" data-cfp-reset>清除筛选</button>
      ${view!=='directory'?'<button class="text-button" data-cfp-view="directory">浏览投稿导航 ↗</button>':''}</div>`;
    output.innerHTML=`<div class="cfp-call-grid">${list.length?list.map(callCard).join(''):empty}</div>`;
  }
  function mount(container) {
    host=container;
    refresh();
    const input=host.querySelector('#cfp-search');
    const dateSelect=host.querySelector('#cfp-date');
    const languageSelect=host.querySelector('#cfp-language');
    const topicSelect=host.querySelector('#cfp-topic');
    let timer;
    const updateQuery=()=>{
      clearTimeout(timer);
      query=input.value.trim();
      timer=setTimeout(()=>{ if(container.isConnected) refresh(); },150);
    };
    host.onclick=event=>{
      const button=event.target.closest('[data-cfp-view], [data-cfp-reset]');
      if (!button) return;
      clearTimeout(timer);
      if (button.hasAttribute('data-cfp-reset')) {
        query=''; language=''; topic=''; dateFilter='all';
        input.value=''; languageSelect.value=''; topicSelect.value=''; dateSelect.value='all';
        refresh();
        input.focus();
        return;
      }
      view=button.dataset.cfpView;
      dateSelect.hidden=view==='directory';
      const nav=host.querySelectorAll('.cfp-nav [data-cfp-view]');
      for (const option of nav) {
        const active=option.dataset.cfpView===view;
        option.setAttribute('aria-pressed',String(active));
        option.classList.toggle('active',active);
        if (active&&option!==button) option.focus();
      }
      refresh();
    };
    input.addEventListener('input',event=>{ if(!event.isComposing) updateQuery(); });
    input.addEventListener('compositionend',updateQuery);
    languageSelect.addEventListener('change',()=>{language=languageSelect.value;refresh();});
    topicSelect.addEventListener('change',()=>{topic=topicSelect.value;refresh();});
    dateSelect.addEventListener('change',()=>{dateFilter=dateSelect.value;refresh();});
  }
  window.CFPWorkbench={render,mount};
})();
