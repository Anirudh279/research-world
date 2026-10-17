from web_research_sim.capture import _extract
from web_research_sim.core import Store


def test_adjacent_numeric_cells_do_not_merge():
    body=b'<table><tr><th>Year</th><th>Output</th></tr><tr><td>2025</td><td>10</td><td>20</td></tr><tr><td>2026</td><td></td><td>30</td></tr></table>'
    text,_,_=_extract(body,'text/html','https://example.org/table')
    assert text.splitlines()==['| Year | Output |','| 2025 | 10 | 20 |','| 2026 | | 30 |']
    assert '1020' not in text and '202510' not in text


def test_table_versions_stay_in_their_time_slices(tmp_path):
    clock=['2026-01-01T00:00:00+00:00'];s=Store(tmp_path,clock=lambda:clock[0]);w=s.create_world('How did output change?')['id']
    snapshots=[];docs=[]
    for day,value in [(1,'10'),(2,'20'),(3,'30')]:
        clock[0]=f'2026-01-0{day}T00:00:00+00:00'
        body=f'<table><caption>Output</caption><tr><th>Period</th><th>Value</th></tr><tr><td>Q1</td><td>{value}</td></tr></table>'.encode()
        text,title,links=_extract(body,'text/html','https://example.org/table')
        doc=s.add_capture(w,url='https://example.org/table',body=body,text=text,captured_at=clock[0],extractor='static-text-v2')
        docs.append(doc);snapshots.append(s.freeze(w)['id'])
    for i,snapshot in enumerate(snapshots):
        r=s.replay(snapshot);assert len(r.records)==i+1
        latest=r.search('Output')[0]
        assert latest['id']==docs[i]['id']
        assert s.objects.get(docs[i]['body_sha256']).endswith(b'</table>')
        assert f'| Q1 | {(i+1)*10} |' in r.open(latest['id'])['text']
