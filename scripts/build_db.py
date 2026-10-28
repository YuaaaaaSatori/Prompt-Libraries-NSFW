#!/usr/bin/env python3
"""从 data/nsfw/ 下的 JSONL.gz 重建 SQLite(含 FTS5 全文索引)。只用 Python 标准库(需 SQLite>=3.34 以支持 trigram 分词)。

用法:
  python3 scripts/build_db.py                                 # -> build/prompt_libraries.db
  python3 scripts/build_db.py --out /tmp/x.db --no-tags-fts   # 指定输出, 跳过标签的全文索引(更小更快)
表:
  categories / subcategories / sources           元数据
  collected_refs(+_fts), ref_subcat              整理的资料(摘要/短提示词/来源链接)
  prompts_n714(+_fts)                            n714 MIT 提示词库
  entries_shuaihui(+_fts)                        ShuaiHui 仓库条目(许可未知, 仅供参考)
  tags(+_fts)                                    Danbooru 标签中英对照(ffdkj / Jio7, MIT)
视图 v_refs: 资料附带子分类名与大类名。
"""
import argparse,gzip,json,os,sqlite3,sys,glob,time
SIDE='nsfw'
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA=os.path.join(ROOT,'data')
def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if line: yield json.loads(line)
SCHEMA='''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE categories(category_no INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT);
CREATE TABLE subcategories(code TEXT PRIMARY KEY, category_no INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, sort INTEGER);
CREATE TABLE sources(key TEXT PRIMARY KEY, name TEXT, url TEXT, license TEXT, version TEXT);
CREATE TABLE collected_refs(id INTEGER PRIMARY KEY, collected_at TEXT, category_no INTEGER, topic TEXT NOT NULL, summary TEXT NOT NULL,
  prompt_text TEXT, source_url TEXT NOT NULL, source_name TEXT, subcat TEXT, models TEXT, source_type TEXT, license_label TEXT);
CREATE TABLE ref_subcat(ref_id INTEGER NOT NULL, subcat_code TEXT NOT NULL, is_primary INTEGER NOT NULL, PRIMARY KEY(ref_id,subcat_code));
CREATE TABLE prompts_n714(id INTEGER PRIMARY KEY, category_no INTEGER, topic TEXT, prompt_text TEXT, description_zh TEXT, is_negative INTEGER, origin_path TEXT, source TEXT);
CREATE TABLE entries_shuaihui(id INTEGER PRIMARY KEY, file TEXT, category_no INTEGER, kind TEXT, section_path TEXT, title TEXT, ref_id TEXT, prompt_text TEXT, is_negative INTEGER, body_text TEXT, source TEXT);
CREATE TABLE tags(id INTEGER PRIMARY KEY, tag TEXT NOT NULL, name_zh TEXT, post_count INTEGER, tag_category TEXT, category_no INTEGER, source TEXT);
'''
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',default=os.path.join(ROOT,'build','prompt_libraries.db')); ap.add_argument('--no-tags-fts',action='store_true')
    a=ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)),exist_ok=True)
    if os.path.exists(a.out): os.remove(a.out)
    db=sqlite3.connect(a.out); db.executescript(SCHEMA); t0=time.time(); counts={}
    for r in rows(os.path.join(DATA,'sources.jsonl.gz')): db.execute('insert into sources values(?,?,?,?,?)',(r['key'],r['name'],r['url'],r['license'],r['version']))
    d=os.path.join(DATA,SIDE)
    for r in rows(f'{d}/categories.jsonl.gz'): db.execute('insert or ignore into categories values(?,?,?)',(r['category_no'],r['title'],r['description']))
    for r in rows(f'{d}/subcategories.jsonl.gz'): db.execute('insert or ignore into subcategories values(?,?,?,?,?)',(r['code'],r['category_no'],r['name'],r['description'],r['sort']))
    n=0
    for r in rows(f'{d}/collected_refs.jsonl.gz'):
        db.execute('insert into collected_refs values(?,?,?,?,?,?,?,?,?,?,?,?)',(r['id'],r['collected_at'],r['category_no'],r['topic'],r['summary'],r['prompt_text'],r['source_url'],r['source_name'],r['subcat'],' / '.join(r['models']),r['source_type'],r['license_label']))
        if r['subcat']: db.execute('insert or ignore into ref_subcat values(?,?,1)',(r['id'],r['subcat']))
        for c in r['subcat_secondary']: db.execute('insert or ignore into ref_subcat values(?,?,0)',(r['id'],c))
        n+=1
    counts['collected_refs']=n; n=0
    for r in rows(f'{d}/prompts_n714.jsonl.gz'):
        db.execute('insert into prompts_n714 values(?,?,?,?,?,?,?,?)',(r['id'],r['category_no'],r['topic'],r['prompt_text'],r['description_zh'],r['is_negative'],r['origin_path'],r['source'])); n+=1
    counts['prompts_n714']=n; n=0
    for r in rows(f'{d}/entries_shuaihui.jsonl.gz'):
        db.execute('insert into entries_shuaihui values(?,?,?,?,?,?,?,?,?,?,?)',(r['id'],r['file'],r['category_no'],r['kind'],r['section_path'],r['title'],r['ref_id'],r['prompt_text'],r['is_negative'],r['body_text'],r['source'])); n+=1
    counts['entries_shuaihui']=n; n=0
    for p in sorted(glob.glob(f'{d}/tags_*.jsonl.gz')):
        for r in rows(p):
            db.execute('insert into tags(tag,name_zh,post_count,tag_category,category_no,source) values(?,?,?,?,?,?)',(r['tag'],r['name_zh'],r['post_count'],r['tag_category'],r['category_no'],r['source'])); n+=1
    counts['tags']=n
    db.execute("create index idx_tags_tag on tags(tag)"); db.execute("create index idx_refs_subcat on collected_refs(subcat)")
    db.execute("create virtual table collected_refs_fts using fts5(topic,summary,prompt_text,content='collected_refs',content_rowid='id',tokenize='trigram')")
    db.execute("create virtual table prompts_n714_fts using fts5(topic,prompt_text,description_zh,content='prompts_n714',content_rowid='id',tokenize='trigram')")
    db.execute("create virtual table entries_shuaihui_fts using fts5(section_path,title,prompt_text,body_text,content='entries_shuaihui',content_rowid='id',tokenize='trigram')")
    ft=['collected_refs','prompts_n714','entries_shuaihui']
    if not a.no_tags_fts:
        db.execute("create virtual table tags_fts using fts5(tag,name_zh,content='tags',content_rowid='id',tokenize='trigram')"); ft.append('tags')
    for t in ft: db.execute(f"insert into {t}_fts({t}_fts) values('rebuild')")
    db.execute("create view v_refs as select r.*, s.name as subcat_name, c.title as category from collected_refs r left join subcategories s on s.code=r.subcat left join categories c on c.category_no=coalesce(s.category_no,r.category_no)")
    for k,v in counts.items(): db.execute('insert into meta values(?,?)',(k,str(v)))
    db.execute('insert into meta values(?,?)',('built_by','scripts/build_db.py'))
    db.commit()
    for t in ft: db.execute(f"insert into {t}_fts({t}_fts) values('integrity-check')")
    ic=db.execute('pragma integrity_check').fetchone()[0]; db.execute('analyze'); db.commit(); db.execute('vacuum'); db.close()
    print('完成',a.out,'integrity_check=',ic,f'{time.time()-t0:.1f}s')
    for k,v in counts.items(): print(' ',k,v)
if __name__=='__main__': main()
