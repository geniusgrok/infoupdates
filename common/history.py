from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from .archive import Archive, atomic_write

LABELS = {("ashare", "morning"): "A股早盘精选", ("ashare", "close"): "A股收盘精选",
          ("usstock", "premarket"): "美股盘前精选", ("usstock", "postmarket"): "美股盘后精选",
          ("weekly", "weekly"): "每周精选"}
STYLE = """
*{box-sizing:border-box}body{margin:0;background:#080a0d;color:#f2f4f6;font:16px/1.7 system-ui,sans-serif}
main{max-width:1180px;margin:auto;padding:28px}h1{margin:0;font-size:30px}h2{font-size:22px}
p,.meta{color:#a4acb6}a{color:#e8b04a}nav{display:flex;gap:14px;margin:16px 0}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}input,select{background:#12161b;color:#f2f4f6;
border:1px solid #2e3640;border-radius:8px;padding:10px;font-size:16px}label{display:flex;align-items:center;gap:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:18px}article{
background:#12161b;border:1px solid #2e3640;border-radius:14px;padding:20px;overflow-wrap:anywhere}
article h3{margin:0;font-size:21px}.meta{font-size:13px}pre{white-space:pre-wrap;font:14px/1.8 system-ui,sans-serif;
margin-top:18px}img{width:100%;max-height:270px;object-fit:contain;margin:16px 0;background:#080a0d}
summary{cursor:pointer;color:#e8b04a}article[hidden]{display:none}.links{display:flex;gap:18px;flex-wrap:wrap}
.tag{font-size:12px;color:#e8b04a}#empty{padding:20px;text-align:center}@media(max-width:480px){main{padding:16px}.grid{grid-template-columns:1fr}}
"""


def build_history(archive: Archive) -> Path:
    from review.events import comparisons, reactions

    escape = html.escape
    now = datetime.now(timezone.utc)
    cards = []
    # 与发布共用数据库写锁，避免并发生成较旧的索引覆盖新索引。
    with archive.connect(write=True):
        rows = sorted(archive.reports(), key=lambda row: row["edition_date"], reverse=True)
        for row in rows:
            data = row["data"]
            label = LABELS[(row["market"], row["session"])]
            if data.get("intraday") and row["session"] == "close":
                label = "A股盘中快照"
            folder = archive.root / row["path"]
            text = (folder / "summary.txt").read_text(encoding="utf-8")
            search = escape(label + " " + text, quote=True)
            path = escape(row["path"], quote=True)
            cards.append(f'''<article class="report" data-date="{row['edition_date']}" data-market="{row['market']}"
data-search="{search}">
<h3>{row['edition_date']} · {label}</h3><div class="meta">生成 {escape(data['generated_at'])}</div>
<a href="{path}/image.png"><img loading="lazy" src="{path}/image.png" alt="{escape(label)}"></a>
<div class="links"><a href="{path}/image.png">查看图片</a><a href="{path}/summary.txt">文字版</a>
<a href="{path}/data.json">完整数据</a></div><details><summary>阅读文案与数据说明</summary><pre>{escape(text)}</pre></details></article>''')

        event_cards = []
        for event in archive.events(through=now):
            lines = comparisons(event)
            status = "发布结果待核实" if any("待核实" in line for line in lines) else "已留存发布数值" if lines else "等待发布" if event["at"] > now.isoformat() else "发布结果待核实"
            reaction = reactions(archive, event, now)
            body = "\n".join(lines + reaction)
            reports = [item for item in event["evidence"] if item["kind"] == "report"]
            evidence = "\n\n".join(f"{item['published_at']} · {item['data']['source']}\n{item['data']['title']}" for item in reports)
            links = " ".join(f'<a href="{escape(item["data"]["url"], quote=True)}" rel="noreferrer">{escape(item["data"]["source"])}</a>'
                             for item in event["evidence"] if item["kind"] != "report")
            event_cards.append(f'''<article><span class="tag">{status}</span><h3>{escape(event['title'])}</h3>
<div class="meta">计划 {escape(event['at'])} · {escape(event['source'])}</div><pre>{escape(body or '暂无可比较的已核实数值。')}</pre>
{links}<details><summary>发布前后的报道 · {len(reports)}条</summary><pre>{escape(evidence or '暂无相关报道。')}</pre></details>
<div class="meta">事件编号 {event['id']}</div></article>''')
        document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>市场精选 · 历史回看</title><style>''' + STYLE + '''</style><main>
<h1>市场精选 · 历史回看</h1><p>按日期、市场和时段回看当时的资讯。图片、文字与完整数据对应同一次生成结果。</p>
<nav><a href="#reports">日报与周报</a><a href="#events">重点事件跟踪</a></nav>
<section id="reports"><div class="filters"><label>日期<input id="day" type="date"></label>
<label>市场<select id="market"><option value="">全部</option><option value="ashare">A股</option>
<option value="usstock">美股</option><option value="weekly">每周精选</option></select></label>
<label>时段<select id="session"><option value="">全部</option><option>早盘</option><option>收盘</option>
<option>盘前</option><option>盘后</option></select></label><input id="query" placeholder="搜索消息或指标" aria-label="搜索消息或指标">
<button id="reset">清空筛选</button></div>
<p id="count"></p><div class="grid">''' + "\n".join(cards) + '''</div><p id="empty" hidden>没有符合条件的内容。</p></section>
<section id="events"><h2>重点事件跟踪</h2><p>只比较发布前已经留存的同口径预期。行情反应附实际观察窗口，不表示由单一事件导致。</p>
<div class="grid">''' + "\n".join(event_cards) + '''</div></section></main><script>
const controls=['day','market','session','query'].map(id=>document.getElementById(id));
function filter(){const [day,market,session,query]=controls;let count=0;
document.querySelectorAll('.report').forEach(card=>{const d=card.dataset;
const visible=(!day.value||d.date===day.value)&&(!market.value||d.market===market.value)&&
(!session.value||card.querySelector('h3').textContent.includes(session.value))&&
(!query.value||d.search.toLowerCase().includes(query.value.toLowerCase()));
card.hidden=!visible;if(visible)count++;});document.getElementById('count').textContent=`共 ${count} 份`;
document.getElementById('empty').hidden=count!==0;}
controls.forEach(control=>control.addEventListener('input',filter));
document.getElementById('reset').addEventListener('click',()=>{controls.forEach(c=>{c.value=''});filter()});filter();
</script></html>'''
        path = archive.root / "index.html"
        atomic_write(path, document.encode("utf-8"))
    return path
