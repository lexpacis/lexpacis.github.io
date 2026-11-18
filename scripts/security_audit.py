"""Focused local checks, not a claim of full penetration testing or legal certification."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from core import ROOT, project_file, read_json
from publication_policy import snapshot_issues

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.errors=[]
        self.policies=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta' and (attrs.get('http-equiv') or '').casefold()=='content-security-policy':
            self.policies.append(set(part.strip() for part in (attrs.get('content') or '').split(';')))
        if any(k.lower().startswith('on') for k in attrs):self.errors.append('HTML 中存在内联事件处理器')
        value=attrs.get('src') if tag in ('script','iframe','embed','object') else attrs.get('href') if tag=='link' and attrs.get('rel')=='stylesheet' else ''
        if value and (value.startswith(('http:','https:','//','data:','javascript:'))):self.errors.append('执行资源或样式依赖外部地址')
        if tag in ('iframe','embed','object'):self.errors.append('不应出现嵌入式外部执行容器')

def audit():
    issues=[];assets=read_json(ROOT/'config/citation-assets.json',[])
    if not assets:issues.append('缺少引注依赖校验清单')
    for asset in assets:
        path=ROOT/'web'/asset['file']
        if not path.resolve().is_relative_to((ROOT/'web/vendor').resolve()):issues.append('依赖路径越界');continue
        output=ROOT/'dist'/asset['file']
        if any(not project_file(ROOT,p) or hashlib.sha256(p.read_bytes()).hexdigest()!=asset['sha256'] for p in (path,output)):
            issues.append('依赖校验失败：'+asset['file'])
    try:
        bundle=(ROOT/'web/vendor/citation-styles.js').read_text(encoding='utf-8')
        if not bundle.startswith('window.LEXATLAS_CSL = '):raise ValueError()
        parsed=json.loads(bundle[len('window.LEXATLAS_CSL = '):].strip().removesuffix(';'))
        for key,value in parsed['styles'].items():
            if value!=(ROOT/'web/vendor'/(key+'.csl')).read_text(encoding='utf-8'):issues.append('打包样式与固定文件不一致：'+key)
        for key,value in parsed['locales'].items():
            if value!=(ROOT/'web/vendor'/('locales-'+key+'.xml')).read_text(encoding='utf-8'):issues.append('打包语言文件不一致：'+key)
    except (ValueError,KeyError,OSError):issues.append('无法验证引注样式包')
    dist=ROOT/'dist'
    if not (dist/'index.html').exists():issues.append('尚未构建网站')
    issues.extend(snapshot_issues(read_json(dist/'data.json'),read_json(ROOT/'config/sources.json',[]),
                                 read_json(ROOT/'config/visibility.json',{})))
    for path in dist.rglob('*'):
        if path.is_symlink():
            issues.append('发布目录存在符号链接')
            continue
        if not path.is_file():continue
        relative=str(path.relative_to(dist)).replace('\\','/')
        if any(part in ('runtime','.git') or part.startswith('.env') for part in path.relative_to(dist).parts) or path.suffix.lower() in ('.sqlite3','.db','.pem','.key','.pfx','.p12'):
            issues.append('发布目录存在私有文件：'+relative)
        if path.suffix=='.html':
            markup=path.read_text(encoding='utf-8')
            parser=AssetParser();parser.feed(markup);issues.extend(relative+': '+message for message in parser.errors)
            required={"default-src 'self'","script-src 'self'","object-src 'none'","base-uri 'none'","form-action 'none'"}
            if not any(required.issubset(policy) for policy in parser.policies):
                issues.append(relative+' 缺少有效的页面安全策略')
    headers=(dist/'_headers').read_text(encoding='utf-8') if (dist/'_headers').exists() else ''
    for directive in ("default-src 'self'","script-src 'self'","object-src 'none'","camera=()","microphone=()"):
        if directive not in headers:issues.append('缺少响应安全策略：'+directive)
    if "unsafe-eval" in headers:issues.append('不得允许 unsafe-eval')
    report={'checks':'dependency hashes in source and output, local execution assets, output privacy, HTML policy',
            'dependency_files':len(assets),'issues':issues,
            'scope_limit':'托管端响应头需从实际站点独立验证'}
    print(json.dumps(report,ensure_ascii=False,indent=2));return report

if __name__=='__main__':raise SystemExit(1 if audit()['issues'] else 0)
