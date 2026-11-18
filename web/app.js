/* Portable static reader. All user data stays in this browser. No model calls. */
(() => {
  'use strict';
  const data = window.LEXATLAS_DATA;
  const academicOnly = data?.publication_scope === 'academic_metadata';
  const verifiedOrigins = data?.publication_scope === 'verified_origins';
  const page = document.getElementById('page-content');
  if (!data || !Array.isArray(data.items)) {
    page.textContent = '资讯数据未能加载。请确认 data.js 与网页位于同一目录，或重新运行 build。';
    return;
  }
  const sources = new Map(data.sources.map(s => [s.id, s]));
  const items = new Map(data.items.map(i => [i.id, i]));
  const moduleNames = Object.fromEntries(data.modules.map(m => [m.id, m.name]));
  const topicNames = Object.fromEntries(data.topics.map(t => [t.id, t.name]));
  const contentRoutes = ['latest', ...data.modules.map(m => m.id), 'saved', 'topic'];
  const knownRoutes = [...contentRoutes, 'topics', 'sources', 'about', 'cite', 'cfp'];
  const pageSize = 18;
  let limit = pageSize;
  let route = 'latest';
  let params = new URLSearchParams();
  let selectedDay = '';
  let calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  let toastTimer;
  let searchTimer;
  let lastTrigger;
  let composingSearch = false;
  let focusSearchOnRoute = false;
  const filterKeys = ['q','source','topic','range','module'];
  const filterControls = {q:'search-input',source:'source-filter',topic:'topic-filter',range:'range-filter',sort:'sort-filter'};
  let stored = [];
  try { stored = JSON.parse(localStorage.getItem('lexatlas.saved.v1') || '[]'); } catch { /* private browsing */ }
  const saved = new Set(Array.isArray(stored) ? stored.filter(id => typeof id === 'string') : []);
  const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const href = value => {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? e(url.href) : '#'; } catch { return '#'; }
  };
  const sourceName = item => sources.get(item.source_id)?.name || item.source_id;
  const title = item => item.title_zh || item.title;
  const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4V3Z"/></svg>';
  const shortDate = (value, unknown = '日期未知') => {
    if (!value) return unknown;
    if (/^\d{4}$/.test(value)) return value + ' 年';
    if (/^\d{4}-\d{2}$/.test(value)) return value.replace('-', ' 年 ') + ' 月';
    return value.slice(0, 10).replaceAll('-', '.');
  };
  const localTime = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:data.site.timezone, hour12:false, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '尚无记录';
  const isoDay = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  const siteDay = () => new Intl.DateTimeFormat('sv-SE',{timeZone:data.site.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const currentMonth = () => {
    const [year,month]=siteDay().split('-').map(Number);
    return new Date(year,month-1,1);
  };
  calendarMonth=currentMonth();
  const countTopic = id => data.items.filter(i => i.topics.includes(id)).length;
  const link = (name, values = {}) => '#/' + name + (Object.keys(values).length ? '?' + new URLSearchParams(values) : '');
  const sourceStatus = source => {
    if (source.status === 'ok' && source.success_at && (Date.now() - Date.parse(source.success_at)) > Math.max(source.poll_hours * 3, 48) * 3600000) return 'stale';
    return source.status;
  };
  const statusName = status => ({ok:'最近采集成功',error:'访问异常',candidate:'候选入口',unverified:'尚未验证',stale:'等待刷新',paused:'暂停采集'}[status] || '未验证');
  const empty = (heading, text, button = '') => `<div class="empty-state"><div class="empty-symbol">⌁</div><h3>${e(heading)}</h3><p>${e(text)}</p>${button}</div>`;
  const saveButton = item => `<button class="save-button ${saved.has(item.id) ? 'saved' : ''}" data-save="${e(item.id)}" aria-label="${saved.has(item.id) ? '取消收藏' : '收藏条目'}：${e(title(item))}" aria-pressed="${saved.has(item.id)}">${icon}</button>`;
  const dateChips = item => ['event','deadline'].map(key => item[key] ? `<span class="event-date-chip">${key === 'event' ? '活动' : '截止'} ${e(shortDate(item[key].date))}</span>` : '').join('');
  const normalizedSearch = value => String(value ?? '').normalize('NFKC').toLowerCase();
  const searchIndex = new Map(data.items.map(item => {
    const titles=normalizedSearch([item.title,item.title_zh].join(' '));
    const creators=normalizedSearch(item.authors.join(' '));
    const doi=normalizedSearch(item.doi);
    const fullText=normalizedSearch([titles,creators,doi,item.summary_zh,sourceName(item),...item.topics.map(id=>topicNames[id])].join(' '));
    return [item.id,{titles,creators,doi,fullText}];
  }));

  function toast(message) {
    const el = document.getElementById('toast');
    el.textContent = message; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => {el.hidden = true;}, 2800);
  }
  function persistSaved() {
    try { localStorage.setItem('lexatlas.saved.v1', JSON.stringify([...saved])); }
    catch { toast('浏览器限制了本地存储，本次收藏仅在当前页面有效。'); }
    document.getElementById('saved-count').textContent = saved.size;
  }
  function downloadJSON(name,value){
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const anchor=document.createElement('a');
    anchor.href=url;anchor.download=name;anchor.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function restoreSaved(file){
    if(!file)return;
    try{
      if(file.size>10_000_000)throw Error('收藏备份文件过大。');
      const backup=JSON.parse(await file.text());
      if(!Array.isArray(backup?.ids)||backup.ids.length>10000||!backup.ids.every(id=>typeof id==='string'&&/^[a-f0-9]{20}$/.test(id))){
        throw Error('请选择从「我的研究清单」导出的收藏 JSON。');
      }
      const before=saved.size;
      for(const id of backup.ids)saved.add(id);
      persistSaved();
      if(route==='saved')render();
      const unknown=backup.ids.filter(id=>!items.has(id)).length;
      toast(`新增 ${saved.size-before} 条收藏${unknown?`，其中备份有 ${unknown} 条已不在当前索引`:''}。`);
    }catch(error){toast(error instanceof SyntaxError?'无法读取收藏 JSON。':error.message);}
  }
  function navigate(name, values = {}, replace = false) {
    const hash = link(name, values);
    if (replace) { history.replaceState(null, '', hash); render(); }
    else if (location.hash === hash) render();
    else location.hash = hash;
  }
  function updateFilter(name, value) {
    if (value) params.set(name,value); else params.delete(name);
    if (name==='q' && !value && params.get('sort')==='relevance') params.delete('sort');
    limit=pageSize;
    history.replaceState(null,'',link(route,Object.fromEntries(params)));
    if (name==='view') render();
    else refreshResults();
  }
  function removeFilters(keys) {
    for (const key of keys) {
      params.delete(key);
      const control=document.getElementById(filterControls[key]);
      if (control) control.value='';
    }
    if (!params.get('q')&&params.get('sort')==='relevance') params.delete('sort');
    limit=pageSize;
    history.replaceState(null,'',link(route,Object.fromEntries(params)));
    refreshResults();
    const control=document.getElementById(filterControls[keys[0]]||'search-input');
    (control?.getClientRects().length?control:document.getElementById('catalog-filter-toggle'))?.focus({preventScroll:true});
  }

  function heading(name, english, description, extra = '') {
    return `<div class="page-heading"><div>${english ? `<div class="eyebrow">${e(english)}</div>` : ''}<h1>${e(name)}</h1>${description ? `<p>${e(description)}</p>` : ''}</div>${extra}</div>`;
  }
  function card(item) {
    const dates = dateChips(item);
    const authorLine=item.authors.length?`<p class="article-author">${e(item.authors.slice(0,4).join('、'))}${item.authors.length>4?' 等':''}</p>`:'';
    return `<article class="article-card" data-item="${e(item.id)}">
      <div class="article-copy">
        <div class="article-kicker">
          <a class="type-label ${e(item.module)}" href="${link(item.module)}">${e(moduleNames[item.module])}</a>
          <a href="${link('latest',{source:item.source_id})}">${e(sourceName(item))}</a>
          ${item.published_at?`<time datetime="${e(item.published_at)}">${e(shortDate(item.published_at))}</time>`:'<span>日期未知</span>'}
        </div>
        <h3><button class="article-title" data-detail="${e(item.id)}" aria-haspopup="dialog">${e(title(item))}</button></h3>
        ${item.title_zh?`<p class="article-original">${e(item.title)}</p>`:''}
        ${authorLine}
        ${item.summary_zh?`<p class="article-description">${e(item.summary_zh)}</p>`:''}
        ${dates?`<div class="event-date-row">${dates}</div>`:''}
        <div class="article-bottom">
          ${item.topics.slice(0,2).map(id=>`<a class="pill" href="${link('topic',{id})}">${e(topicNames[id]||id)}</a>`).join('')}
          <div class="article-actions">
            <button class="cite-this" data-cite="${e(item.id)}">引注</button>
            <a class="read-link" href="${href(item.url)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>
          </div>
        </div>
      </div>
      ${saveButton(item)}
    </article>`;
  }
  function scopedItems() {
    return data.items.filter(item => {
      if (route==='saved') return saved.has(item.id);
      if (route==='topic'&&params.get('id')) return item.topics.includes(params.get('id'));
      return !Object.hasOwn(moduleNames,route)||item.module===route;
    });
  }
  function filterItems() {
    const sourceFilter=params.get('source');
    const topicFilter=params.get('topic');
    const moduleFilter=params.get('module');
    const range=Number(params.get('range'));
    const query=normalizedSearch(params.get('q')).trim().replace(/^(https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/,'');
    const tokens=query.split(/\s+/).filter(Boolean);
    const filterNow=Date.now();
    const matches = scopedItems().filter(item => {
      if (sourceFilter && item.source_id !== sourceFilter) return false;
      if (topicFilter && !item.topics.includes(topicFilter)) return false;
      if (range) {
        if (!item.published_at || !['day','instant'].includes(item.date_precision)) return false;
        const age = (filterNow - Date.parse(item.published_at)) / 86400000;
        if (!Number.isFinite(age) || age < -1 || age > range) return false;
      }
      return tokens.every(token => searchIndex.get(item.id).fullText.includes(token));
    });
    const result=moduleFilter ? matches.filter(item=>item.module===moduleFilter):[...matches];
    const sort = currentSort();
    const latest = (a,b) => (b.published_at || '').localeCompare(a.published_at || '') || b.discovered_at.localeCompare(a.discovered_at);
    const relevance = item => {
      const index=searchIndex.get(item.id);
      return (index.doi===query ? 50 : 0) + tokens.reduce((score,token)=>score+
        (index.titles.includes(token)?6:0)+(index.creators.includes(token)?4:0)+(index.doi.includes(token)?8:0),0);
    };
    if (sort === 'discovered') result.sort((a,b) => b.discovered_at.localeCompare(a.discovered_at));
    else if (sort === 'oldest') result.sort((a,b) => (a.published_at || '9999').localeCompare(b.published_at || '9999'));
    else if (sort === 'relevance') result.sort((a,b) => relevance(b)-relevance(a)||latest(a,b));
    else result.sort(latest);
    return {result,matches};
  }
  function currentSort() {
    const explicit=params.get('sort');
    if (['published','oldest','discovered'].includes(explicit)) return explicit;
    return params.get('q') ? 'relevance' : 'published';
  }
  function options(entries, selected) {
    return entries.map(([value,label]) => `<option value="${e(value)}" ${selected === value ? 'selected' : ''}>${e(label)}</option>`).join('');
  }
  function catalogToolbar() {
    const available=new Set(scopedItems().map(item=>item.source_id));
    const sourcesInView=data.sources.filter(source=>available.has(source.id));
    const topicOptions=data.topics.map(topic=>[topic.id,topic.short]);
    return `<div class="catalog-controls">
      <form id="catalog-search" class="catalog-search" role="search">
        <label for="search-input" class="visually-hidden">搜索标题、作者、DOI 或关键词</label>
        <span class="search-symbol" aria-hidden="true">⌕</span>
        <input type="search" id="search-input" name="q" maxlength="200" autocomplete="off"
          placeholder="搜索标题、作者、DOI 或关键词" value="${e(params.get('q')||'')}">
        <kbd aria-hidden="true">/</kbd><button type="submit">检索</button>
      </form>
      ${route==='latest'? `<nav class="catalog-topics" aria-label="浏览专题"><span>按专题浏览</span>
        ${data.topics.map(topic=>`<a href="${link('topic',{id:topic.id})}">${e(topic.short)}</a>`).join('')}
      </nav>`:''}
      <button type="button" class="catalog-filter-toggle" id="catalog-filter-toggle" aria-expanded="false" aria-controls="catalog-filter-panel">筛选与排序 <span aria-hidden="true">⌄</span></button>
      <div class="catalog-filters" id="catalog-filter-panel">
        <label>信源<select id="source-filter" class="filter-select">${options([['','全部信源'],...sourcesInView.map(s=>[s.id,s.name])],params.get('source')||'')}</select></label>
        ${route!=='topic'?`<label>专题<select id="topic-filter" class="filter-select">${options([['','全部专题'],...topicOptions],params.get('topic')||'')}</select></label>`:''}
        <label>发布日期<select id="range-filter" class="filter-select">${options([['','全部时间'],['7','近 7 天'],['30','近 30 天'],['90','近 90 天']],params.get('range')||'')}</select></label>
        <label>排序<select id="sort-filter" class="filter-select"></select></label>
      </div>
      <div id="catalog-active-filters" class="catalog-active-filters" hidden></div>
    </div>`;
  }
  function newsStatus() {
    const registered=data.sources.filter(s=>s.module==='news'&&s.enabled);
    const good=registered.filter(s=>sourceStatus(s)==='ok').length;
    const bad=registered.filter(s=>sourceStatus(s)==='error').length;
    return `<p class="saved-notice news-status">已接入 ${registered.length} 个新闻入口 · 最近采集成功 ${good} 个${bad?` · 访问异常 ${bad} 个`:''}。${good?'':'自动更新尚未验证成功；现有条目为首次核对的官方目录信息。'}报道保留机构来源，不等于裁判或法律认定。<a href="#/sources">查看信源状态 ↗</a></p>`;
  }
  function emptyStream() {
    if (route==='saved'&&!saved.size) {
      return empty('还没有收藏','点任意资料旁的书签，即可把它加入收藏。','<a class="secondary-button" href="#/latest">浏览资料库 ↗</a>');
    }
    if (filterKeys.some(key=>params.get(key))) {
      return empty('未找到匹配资料','可以换用更短的关键词，或清除筛选后重新检索。','<button class="secondary-button" data-clear-filters>清除筛选</button>');
    }
    return empty('暂无收录资料','可回到资料库浏览其他研究文献、案件或学术活动。','<a class="secondary-button" href="#/latest">浏览资料库 ↗</a>');
  }
  function stream() {
    const showTypes=['latest','topic','saved'].includes(route);
    const tabs=[['','全部'],...data.modules.filter(m=>!data.paused_modules?.includes(m.id)).map(m=>[m.id,m.name])];
    return `<section class="stream" aria-label="资料检索与结果">
      ${catalogToolbar()}
      ${showTypes?`<div class="catalog-types" role="group" aria-label="资料类型">
        ${tabs.map(([id,name])=>`<button type="button" data-module="${e(id)}" aria-pressed="false">${e(name)} <span data-facet-count></span></button>`).join('')}
      </div>`:''}
      <div class="catalog-result-head">
        <strong id="catalog-total" class="stream-total" role="status" tabindex="-1"></strong>
        ${route==='topic'&&topicNames[params.get('id')]?`<a href="feeds/${e(params.get('id'))}.xml">专题 RSS ↗</a>`:''}
        ${Object.hasOwn(moduleNames,route)?`<a href="feeds/modules/${e(route)}.xml">栏目 RSS ↗</a>`:''}
      </div>
      <div class="article-list" id="catalog-items"></div><div id="catalog-pagination"></div>
    </section>`;
  }
  function refreshResults(focusNext=false) {
    const output=document.getElementById('catalog-items');
    const calendar=document.getElementById('catalog-calendar');
    if (!output&&!calendar) return;
    const {result,matches}=filterItems();
    const total=document.getElementById('catalog-total');
    if(total) total.textContent=`找到 ${result.length} 条资料`;
    document.querySelectorAll('[data-module]').forEach(button=>{
      const module=button.dataset.module;
      button.setAttribute('aria-pressed',String(module===(params.get('module')||'')));
      button.querySelector('[data-facet-count]').textContent=module?matches.filter(item=>item.module===module).length:matches.length;
    });

    const sortOptions=[['published','最新发表'],['discovered','最近收录'],['oldest','最早发表']];
    if (params.get('q')) sortOptions.unshift(['relevance','相关度']);
    const sort=document.getElementById('sort-filter');
    if (sort.options.length!==sortOptions.length) sort.innerHTML=options(sortOptions,currentSort());
    sort.value=currentSort();

    const filters=document.getElementById('catalog-active-filters');
    const chips=[
      ['q','搜索',params.get('q')],
      ['source','信源',sources.get(params.get('source'))?.name||params.get('source')],
      ['topic','专题',topicNames[params.get('topic')]||params.get('topic')],
      ['range','时间',params.get('range')?`近 ${params.get('range')} 天`:''],
      ['module','类型',moduleNames[params.get('module')]]
    ].filter(([, ,value])=>value);
    filters.hidden=!chips.length;
    filters.innerHTML=chips.map(([key,label,value])=>`<button data-remove-filter="${key}" aria-label="撤销${e(label)}条件：${e(value)}">${e(label)} · ${e(value)} <span aria-hidden="true">×</span></button>`).join('')+
      (chips.length?'<button class="text-button" data-clear-filters>全部清除</button>':'');
    if (calendar) {
      calendar.innerHTML=calendarView(result);
      return;
    }
    output.innerHTML=result.length?result.slice(0,limit).map(card).join(''):emptyStream();
    const pagination=document.getElementById('catalog-pagination');
    pagination.innerHTML=result.length>limit?`<button class="more-button" data-load-more>再显示 ${Math.min(result.length-limit,pageSize)} 条 ↓</button>`:'';
    if (focusNext) output.children[limit-pageSize]?.querySelector('.article-title')?.focus();
  }
  function rail() {
    if (academicOnly) {
      return `<aside class="rail research-rail" aria-label="研究工具">
        <section class="rail-section"><div class="rail-heading"><h2>投稿导航</h2><a href="#/cfp">浏览 ↗</a></div><p>${data.cfp.journals.length} 种期刊的投稿入口</p></section>
        <section class="rail-section"><div class="rail-heading"><h2>引注转换</h2><a href="#/cite">打开 ↗</a></div><p>由书目信息生成脚注</p></section>
        <section class="rail-section rail-resource"><div class="rail-heading"><h2>来源与订阅</h2></div><a href="#/sources">书目信源 ↗</a><a href="feed.xml">订阅资料 RSS ↗</a></section>
      </aside>`;
    }
    const today=siteDay();
    const calls=(data.cfp?.calls||[]).filter(call=>call.kind!=='discovery'&&call.deadline>=today);
    calls.sort((a,b)=>a.deadline.localeCompare(b.deadline));
    const agenda=data.items.filter(item=>item.module==='events'&&(item.deadline?.date>=today||item.event?.date>=today));
    return `<aside class="rail research-rail" aria-label="研究工具与日程">
      <section class="rail-section">
        <div class="rail-heading"><h2>近期征稿</h2><a href="#/cfp">全部 ↗</a></div>
        ${calls.length?calls.slice(0,3).map(call=>`<div class="rail-call"><time datetime="${e(call.deadline)}">${e(shortDate(call.deadline))}</time><a href="${href(call.url)}" target="_blank" rel="noopener noreferrer">${e(call.title)}</a></div>`).join(''):'<p>查看期刊投稿导航和已核对的征稿公告。</p>'}
      </section>
      <section class="rail-section">
        <div class="rail-heading"><h2>学术日历</h2><a href="#/events?view=calendar">打开 ↗</a></div>
        <p>${agenda.length?`有 ${agenda.length} 项日期明确的待办活动。`:'会议和论坛公告可在学术活动中浏览。'}</p>
        <a class="text-button" href="calendar.ics">订阅学术日历 ICS ↗</a>
      </section>
      <section class="rail-section rail-resource">
        <div class="rail-heading"><h2>来源与订阅</h2></div>
        <a href="#/sources">信源与覆盖范围 ↗</a><a href="feed.xml">订阅资料 RSS ↗</a>
      </section>
    </aside>`;
  }
  function topicDirectory() {
    return heading('循专题而行','RESEARCH PATHWAYS','把不同来源的文献、案件和评论放回同一研究脉络。')+`<div class="topic-grid">${data.topics.map((topic,index)=>`<a class="topic-large" href="${link('topic',{id:topic.id})}"><span class="topic-number">0${index+1} / RESEARCH TOPIC</span><h2>${e(topic.name)}</h2><div class="topic-meta"><span>${countTopic(topic.id)} 条相关资料</span><span>进入专题 ↗</span></div></a>`).join('')}</div>`;
  }
  function sourcesPage() {
    const enabled=data.sources.filter(s=>s.enabled).length;
    const good=data.sources.filter(s=>sourceStatus(s)==='ok').length;
    return heading('信源目录','','查看来源机构、收录范围及最近访问状态。')+`<div class="source-summary"><div class="metric-block"><strong>${enabled}</strong><span>已启用入口</span></div><div class="metric-block"><strong>${good}</strong><span>最近采集成功</span></div><div class="metric-block"><strong>${data.sources.filter(s=>sourceStatus(s)==='error').length}</strong><span>最近访问异常</span></div><div class="metric-block"><strong>${data.sources.filter(s=>!s.enabled).length}</strong><span>候选或暂停</span></div></div><div class="source-grid">${data.sources.map(s=>{const status=sourceStatus(s);return `<article class="source-card"><div class="source-card-top"><h3>${e(s.name)}</h3><span class="status-pill ${status}">${statusName(status)}</span></div><div class="organization">${e(s.organization)}</div><p>${e(s.scope)}</p><dl><dt>来源类型</dt><dd>${e({official_institution:'官方机构',official_news_service:'官方机构新闻服务',academic_commentary:'学术评论平台',academic_society:'学术团体',scholarly_index:'学术元数据索引'}[s.authority]||s.authority)}</dd><dt>采集方式</dt><dd>${e(s.kind.toUpperCase())} · ${s.poll_hours} 小时最短间隔</dd><dt>当前收录</dt><dd>${s.item_count} 条</dd><dt>最近尝试</dt><dd>${e(localTime(s.attempted_at))}</dd><dt>最近成功</dt><dd>${e(localTime(s.success_at))}</dd></dl>${s.error?`<p class="source-error">最近异常：${e(s.error)}</p>`:''}<a class="text-button" href="${href(s.homepage)}" target="_blank" rel="noopener noreferrer">访问原始网站 ↗</a></article>`}).join('')}</div>`;
  }
  function aboutPage() {
    return heading('收录范围、隐私与纠错','','')+`<div class="about-layout">
      <article class="prose">
        <h2>资料范围</h2>
        <p>${e(data.site.name)}面向高校师生和研究者，提供国际法资料索引、投稿导航和引注工具。${verifiedOrigins?'涉华资讯仅索引经过核验的中国政府或中国官方媒体原发页面。来源或主题无法核实的条目暂不展示。':''}引用前请核对原始来源。</p>
        <h3>隐私与外部服务</h3>
        <p>浏览无需账号。收藏、引注清单和本地导入文件留在当前浏览器。你主动查 DOI 时，浏览器会向 Crossref 发送该 DOI。托管服务 GitHub Pages 会为安全目的记录访客 IP。</p>
        <p><a href="https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages#data-collection" target="_blank" rel="noopener noreferrer">GitHub Pages 数据收集说明 ↗</a></p>
        <h3>纠错与权利通知</h3>
        <p>书目或链接有误，可报告本站条目地址或 DOI。公开工单请只填写必要的书目信息。涉及个人信息、保密材料或权利证明时，请使用托管方的私下报告渠道。</p>
        <p><a href="https://github.com/lexpacis/lexpacis.github.io/issues/new" target="_blank" rel="noopener noreferrer">提交书目纠错 ↗</a> · <a href="https://support.github.com/contact/report-abuse" target="_blank" rel="noopener noreferrer">向托管方报告 ↗</a> · <a href="https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy" target="_blank" rel="noopener noreferrer">版权通知 ↗</a></p>
      </article>
      <aside class="status-panel">
        <h3>发布状态</h3>
        <p><strong>数据生成时间</strong>${e(localTime(data.generated_at))}</p>
        <p><strong>当前公开书目</strong>${data.items.length} 条</p>
        <a class="secondary-button" href="#/sources">查看信源状态 ↗</a>
      </aside>
    </div>`;
  }
  function calendarView(result) {
    const start=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth(),1);
    const offset=(start.getDay()+6)%7;
    const first=new Date(start); first.setDate(1-offset);
    const dated=[];
    result.forEach(item=>['event','deadline'].forEach(key=>{if(item[key])dated.push({item,key,date:item[key].date});}));
    const dayMap=new Map(); dated.forEach(value=>{if(!dayMap.has(value.date))dayMap.set(value.date,[]);dayMap.get(value.date).push(value);});
    const today=siteDay();
    const cells=Array.from({length:42},(_,index)=>{const day=new Date(first);day.setDate(first.getDate()+index);const iso=isoDay(day);const entries=dayMap.get(iso)||[];return `<button class="calendar-day ${day.getMonth()!==start.getMonth()?'outside':''} ${iso===today?'today':''} ${iso===selectedDay?'selected':''}" data-day="${iso}" aria-pressed="${iso===selectedDay}" aria-label="${iso}，${entries.length} 项活动或截止日期"><span class="day-number">${day.getDate()}</span>${entries.length?`<span class="day-events">${entries.length} 项 ${entries.some(i=>i.key==='deadline')?'· 截止':''}</span>`:''}</button>`;}).join('');
    const selected=selectedDay?dated.filter(d=>d.date===selectedDay):dated.filter(d=>d.date.slice(0,7)===isoDay(start).slice(0,7));
    const unique=[...new Map(selected.map(d=>[d.item.id,d.item])).values()];
    return `<div class="calendar-layout"><div><div class="calendar"><div class="calendar-top"><h2>${start.getFullYear()} 年 ${start.getMonth()+1} 月</h2><div class="calendar-controls"><button data-month="prev" aria-label="上个月">←</button><button data-month="today">本月</button><button data-month="next" aria-label="下个月">→</button></div></div><div class="calendar-grid">${['一','二','三','四','五','六','日'].map(d=>`<div class="calendar-weekday">周${d}</div>`).join('')}${cells}</div><div class="calendar-legend"><span>仅显示有明确原文日期的活动</span><a class="text-button" href="calendar.ics">订阅 ICS ↗</a></div></div>${!dated.length?`<div class="rail-empty" style="margin-top:15px">当前尚无已确认日期的活动。活动列表仍可浏览已收录公告；不会将公告发布日期填入日历。</div>`:''}</div><section class="calendar-list"><h3>${selectedDay?e(shortDate(selectedDay)):'本月安排'}</h3>${unique.length?unique.map(card).join(''):empty('暂无已确认日程','可以查看其他日期，或切换到公告列表。')}${selectedDay?'<button class="text-button" data-clear-day>查看本月全部</button>':''}</section></div>`;
  }
  function openDetail(id, trigger) {
    const item=items.get(id); if(!item)return;
    const source=sources.get(item.source_id);
    lastTrigger=trigger || document.activeElement;
    document.getElementById('detail-content').innerHTML=`<div class="eyebrow">${e(moduleNames[item.module])} / ${e(source.name)}</div><h2 class="detail-heading" id="detail-title">${e(title(item))}</h2>${item.title_zh?`<p class="detail-original">${e(item.title)}</p>`:''}<p class="basis-badge">${item.ai?'AI 自动整理 · 基于'+e(item.evidence_basis):'来源元数据 · 尚无 AI 摘要'}</p><div class="detail-body">${e(item.summary_zh || '本条目提供原始资料索引。当前没有足够的内容依据生成实质摘要，请访问原文。')}</div>${dateChips(item)?`<div class="event-date-row">${dateChips(item)}</div>`:''}<dl class="facts"><dt>原始出处</dt><dd>${e(source.organization)}</dd><dt>作者</dt><dd>${e(item.authors.join('、') || '来源未提供')}</dd><dt>发布日期</dt><dd>${e(shortDate(item.published_at))} ${item.date_precision==='month'?'（精确到月）':item.date_precision==='year'?'（精确到年）':''}</dd><dt>首次发现</dt><dd>${e(localTime(item.discovered_at))}</dd><dt>研究专题</dt><dd>${item.topics.map(t=>e(topicNames[t])).join(' / ') || '综合国际法'}</dd>${item.doi?`<dt>DOI</dt><dd>${e(item.doi)}</dd>`:''}<dt>材料性质</dt><dd>${e(item.material_type || moduleNames[item.module])}</dd><dt>输入依据</dt><dd>${e(item.evidence_basis)}；未核验全文</dd>${item.ai?`<dt>整理模型</dt><dd>${e(item.ai.model)}</dd>`:''}${item.provenance.length>1?`<dt>其他入口</dt><dd>${item.provenance.map(p=>e(sources.get(p.source_id)?.name || p.source_id)).join(' / ')}</dd>`:''}</dl><div class="detail-actions"><a class="primary-button" href="${href(item.url)}" target="_blank" rel="noopener noreferrer">访问原始来源 ↗</a><button class="secondary-button" data-save="${e(id)}" aria-pressed="${saved.has(id)}">${saved.has(id)?'已收藏 · 取消':'加入研究清单'}</button><button class="text-button" data-cite="${e(id)}">生成引注 ↗</button><a class="text-button" href="items/${e(id)}.html">独立页面 ↗</a></div><p class="fine-print">自动整理与字段检查不保证完整性或法律判断准确。官方新闻稿、当事方主张、裁判文件与学术观点具有不同性质，请回到原始资料核查。</p>`;
    const dialog=document.getElementById('detail-dialog'); if(!dialog.open)dialog.showModal();
  }
  function render() {
    const raw=location.hash.replace(/^#\/?/,'') || 'latest';
    const [name,query='']=raw.split('?');
    route=knownRoutes.includes(name)?name:'latest'; params=new URLSearchParams(query);
    if(params.has('module')&&!data.modules.some(m=>m.id===params.get('module')&&!data.paused_modules?.includes(m.id)))params.delete('module');
    document.querySelectorAll('[data-route]').forEach(a=>{a.classList.toggle('active',a.dataset.route===route);if(a.dataset.route===route)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    document.querySelectorAll('[data-topic]').forEach(a=>a.classList.toggle('active',route==='topic'&&params.get('id')===a.dataset.topic));
    let html=''; let pageTitle='最新资讯';
    if(route==='cite'){
      if(items.has(params.get('item')))window.CitationWorkbench.setItem(items.get(params.get('item')));
      html=heading('引注转换','','从书目信息生成可复制的脚注。')
        +'<div id="cite-module"><p role="status">正在加载本地引注组件…</p></div>';
      pageTitle='引注转换';
    }
    else if(route==='cfp'){
      pageTitle=academicOnly?'期刊投稿导航':'CFP 征稿';
      const description=academicOnly?'查找中英文期刊的原始投稿入口。':'查看征稿公告、截止日期和中英文期刊投稿入口。';
      html=heading(pageTitle,'',description)+'<div id="cfp-module">'+window.CFPWorkbench.render(data)+'</div>';
    }
    else if(route==='sources'){html=sourcesPage();pageTitle='信源目录';}
    else if(route==='about'){html=aboutPage();pageTitle='关于与更新状态';}
    else if(route==='topics'){html=topicDirectory();pageTitle='研究专题';}
    else {
      const descriptions={latest:academicOnly?'检索国际法期刊书目，使用投稿导航和引注工具。':'检索国际法文献、司法资料与学术活动。',research:'查看论文的题名、作者、DOI 与出版信息。',events:'浏览会议、论坛和学术活动公告。',official:'查看原始机构公告和官方资料。',cases:'查看国际司法与仲裁机构发布的案件资料。',commentary:'查看学术平台的评论和讨论。',saved:'清除网站数据会删除收藏，清理前请先备份。'};
      pageTitle=moduleNames[route] || (route==='saved'?'我的收藏':route==='topic'?(topicNames[params.get('id')] || '研究专题'):'国际法资料库');
      const calendar=params.get('view')==='calendar';
      const extra=route==='events'?`<div class="view-switch" role="group" aria-label="活动视图"><button data-view="list" aria-pressed="${!calendar}" class="${!calendar?'active':''}">公告列表</button><button data-view="calendar" aria-pressed="${calendar}" class="${calendar?'active':''}">学术日历</button></div>`:'';
      html=heading(pageTitle,'',descriptions[route] || '',extra);
      if(data.paused_modules?.includes(route)){
        html=heading(pageTitle,'','本站目前收录国际法学术资料。');
        html+=empty('此栏目暂停发布','仍可使用书目检索、期刊投稿导航和引注工具。','<a class="secondary-button" href="#/research">浏览学术书目 ↗</a>');
        page.innerHTML=html;
        document.title=`${pageTitle} — ${data.site.name} ${data.site.english_name}`;
        return;
      }
      if(route==='news')html+=newsStatus();
      if(route==='saved'){
        const indexed=data.items.filter(i=>saved.has(i.id)).length;
        html+=`<div class="saved-actions"><button class="secondary-button" data-export-saved ${saved.size?'':'disabled'}>备份收藏 JSON</button><button class="secondary-button" data-import-saved>恢复收藏</button><input id="saved-backup-file" type="file" accept=".json,application/json" hidden><button class="secondary-button" data-export-saved-csl ${indexed?'':'disabled'}>导出 Zotero 书目</button><button class="text-button" data-clear-saved ${saved.size?'':'disabled'}>清空收藏</button></div><p class="saved-notice">已收藏 ${saved.size} 条，${indexed} 条在当前索引中。恢复会合并收藏，文件仅在浏览器内读取。</p>`;
      }
      if(route==='events'&&params.get('view')==='calendar')html+=catalogToolbar()+'<div id="catalog-calendar"></div>';
      else html+=`<div class="content-columns${route==='latest'?' with-rail':''}">${stream()}${route==='latest'?rail():''}</div>`;
    }
    page.innerHTML=html;
    document.title=`${pageTitle} — ${data.site.name} ${data.site.english_name}`;
    refreshResults();
    if(route==='cite'){
      const container=document.getElementById('cite-module');
      window.CitationWorkbench.loadEngine().then(()=>{
        if(!container.isConnected)return;
        container.innerHTML=window.CitationWorkbench.render();
        window.CitationWorkbench.mount(container,toast);
      }).catch(error=>{
        if(container.isConnected)container.textContent=error.message;
      });
    }
    if(route==='cfp')window.CFPWorkbench.mount(document.getElementById('cfp-module'));
    if(route!=='cite'&&params.get('item')&&items.has(params.get('item')))openDetail(params.get('item'));
  }

  document.getElementById('topic-nav').innerHTML=data.topics.map(t=>`<a href="${link('topic',{id:t.id})}" data-topic="${e(t.id)}"><span class="topic-dot"></span>${e(t.short)}</a>`).join('');
  document.getElementById('saved-count').textContent=saved.size;
  for (const link of document.querySelectorAll('[data-route]')) {
    if (Object.hasOwn(moduleNames,link.dataset.route)) link.hidden=data.paused_modules?.includes(link.dataset.route)??false;
    if (academicOnly&&link.dataset.route==='cfp') link.lastChild.textContent='投稿导航';
  }
  document.querySelector('[data-calendar-subscribe]').hidden=academicOnly;
  document.getElementById('footer-updated').textContent='数据生成 '+localTime(data.generated_at)+' · 原始资料版权归各来源';
  const navToggle=document.getElementById('nav-toggle');
  function setNavigation(open) {
    navToggle.setAttribute('aria-expanded',String(open));
    document.querySelector('.sidebar').classList.toggle('menu-open',open);
  }
  function focusCatalog() {
    const input=document.getElementById('search-input');
    if (input) { input.focus(); return; }
    focusSearchOnRoute=true;
    navigate('latest');
  }
  function submitQuery(input, immediate=false) {
    clearTimeout(searchTimer);
    const query=input.value.trim().slice(0,200);
    if (immediate) updateFilter('q',query);
    else searchTimer=setTimeout(()=>updateFilter('q',query),180);
  }

  document.addEventListener('click',event=>{
    const target=event.target.closest('button,a'); if(!target)return;
    if (target.classList.contains('skip-link')) {
      event.preventDefault();
      document.getElementById('main').focus();
      return;
    }
    if (target.closest('.sidebar-content a, .mobile-navigation a')) setNavigation(false);
    if (target.id==='catalog-filter-toggle') {
      const open=target.getAttribute('aria-expanded')!=='true';
      target.setAttribute('aria-expanded',String(open));
      return;
    }
    if(target.dataset.save){
      const id=target.dataset.save;
      const isSaved=!saved.has(id);
      if (isSaved) saved.add(id); else saved.delete(id);
      persistSaved();
      for (const button of document.querySelectorAll('[data-save]')) {
        if (button.dataset.save!==id) continue;
        button.setAttribute('aria-pressed',String(isSaved));
        if (button.classList.contains('save-button')) {
          button.classList.toggle('saved',isSaved);
          button.setAttribute('aria-label',`${isSaved?'取消收藏':'收藏条目'}：${title(items.get(id))}`);
        } else button.textContent=isSaved?'已收藏 · 取消':'加入研究清单';
      }
      if (route==='saved') {
        render();
        if (!dialog.open) document.getElementById('catalog-total')?.focus();
      }
      toast(isSaved?'已加入收藏':'已取消收藏');
    } else if(target.dataset.cite){const chosen=items.get(target.dataset.cite);if(chosen){document.getElementById('detail-dialog').close();window.CitationWorkbench.setItem(chosen);navigate('cite',{item:chosen.id});}}
    else if(target.dataset.detail)openDetail(target.dataset.detail,target);
    else if(target.hasAttribute('data-module'))updateFilter('module',target.dataset.module);
    else if(target.dataset.removeFilter) {clearTimeout(searchTimer);removeFilters([target.dataset.removeFilter]);}
    else if(target.hasAttribute('data-clear-filters')) {clearTimeout(searchTimer);removeFilters([...filterKeys,'sort']);}
    else if(target.hasAttribute('data-load-more')) {limit+=pageSize;refreshResults(true);}
    else if(target.dataset.view)updateFilter('view',target.dataset.view==='list'?'':target.dataset.view);
    else if(target.dataset.month){
      if(target.dataset.month==='today') calendarMonth=currentMonth();
      else calendarMonth=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()+(target.dataset.month==='next'?1:-1),1);
      selectedDay='';
      refreshResults();
      document.querySelector(`[data-month="${target.dataset.month}"]`)?.focus({preventScroll:true});
    }
    else if(target.dataset.day){
      selectedDay=target.dataset.day;
      refreshResults();
      document.querySelector(`[data-day="${selectedDay}"]`)?.focus({preventScroll:true});
    }
    else if(target.hasAttribute('data-clear-day')){
      const previous=selectedDay;
      selectedDay='';
      refreshResults();
      document.querySelector(`[data-day="${previous}"]`)?.focus({preventScroll:true});
    }
    else if(target.hasAttribute('data-export-saved')){
      const exported={exported_at:new Date().toISOString(),site:data.site.english_name,ids:[...saved],items:data.items.filter(i=>saved.has(i.id))};
      downloadJSON('lexpacis-collection.json',exported);
    } else if(target.hasAttribute('data-import-saved'))document.getElementById('saved-backup-file').click();
    else if(target.hasAttribute('data-export-saved-csl')){
      const records=data.items.filter(item=>saved.has(item.id)).map(item=>({...window.CitationWorkbench.fromItem(item),id:item.id}));
      downloadJSON('lexpacis-zotero.csl.json',records);
    } else if(target.hasAttribute('data-clear-saved')){
      if(!window.confirm('确定清空当前浏览器的全部收藏？建议先备份。'))return;
      saved.clear();persistSaved();render();toast('已清空当前浏览器的收藏');
    }
  });
  document.addEventListener('compositionstart',event=>{
    if(event.target.id==='search-input') {clearTimeout(searchTimer);composingSearch=true;}
  });
  document.addEventListener('compositionend',event=>{
    if(event.target.id==='search-input') {composingSearch=false;submitQuery(event.target);}
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='search-input'&&!event.isComposing&&!composingSearch)submitQuery(event.target);
  });
  document.addEventListener('submit',event=>{
    if(event.target.id!=='catalog-search')return;
    event.preventDefault();
    const input=event.target.elements.q;
    if(!composingSearch)submitQuery(input,true);
  });
  document.addEventListener('change',event=>{
    if(event.target.id==='saved-backup-file'){restoreSaved(event.target.files[0]);return;}
    const filters={'source-filter':'source','topic-filter':'topic','range-filter':'range','sort-filter':'sort'};
    if(filters[event.target.id])updateFilter(filters[event.target.id],event.target.value);
  });
  const dialog=document.getElementById('detail-dialog');
  document.getElementById('close-dialog').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{if(event.target===dialog){const bounds=dialog.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)dialog.close();}});
  dialog.addEventListener('close',()=>{
    if(lastTrigger?.isConnected)lastTrigger.focus({preventScroll:true});
    else document.getElementById('catalog-total')?.focus({preventScroll:true});
  });
  document.addEventListener('keydown',event=>{
    const active=document.activeElement;
    if(event.key==='Escape'&&navToggle.getAttribute('aria-expanded')==='true'){
      setNavigation(false);navToggle.focus();return;
    }
    if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.isComposing&&
       !active?.matches('input,textarea,select,[contenteditable]')&&!dialog.open){
      event.preventDefault();
      focusCatalog();
    }
  });
  navToggle.addEventListener('click',()=>setNavigation(navToggle.getAttribute('aria-expanded')!=='true'));
  document.getElementById('header-search').addEventListener('click',focusCatalog);
  window.addEventListener('hashchange',()=>{
    clearTimeout(searchTimer);
    composingSearch=false;
    if(dialog.open)dialog.close();
    limit=pageSize;
    render();
    window.scrollTo({top:0,behavior:'instant'});
    if(focusSearchOnRoute){focusSearchOnRoute=false;document.getElementById('search-input')?.focus();}
  });
  render();
})();
