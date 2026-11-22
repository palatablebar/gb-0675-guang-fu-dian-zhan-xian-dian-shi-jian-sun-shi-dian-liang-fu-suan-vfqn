from fastapi.testclient import TestClient
from app import main,store
from app.engine import evaluate
from app.store import save_rerun
from app.errors import BusinessError
import pytest

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)

def test_rerun_heat_idempotent(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    a=c.post('/api/runs',json={'dataset':'heat'}).json();rid=a['manifest']['run_id']
    b=c.post('/api/runs/'+rid+'/rerun',json={}).json()
    assert b['manifest']['run_id']==rid;assert b['manifest']['sha256']==a['manifest']['sha256']
    assert len(c.get('/api/runs').json())==1

def test_reviewed_run_not_overwritten_by_rerun(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    a=c.post('/api/runs',json={}).json();rid=a['manifest']['run_id']
    assert c.post('/api/runs/'+rid+'/review',json={'revision':1}).json()['revision']==2
    before=c.get('/api/runs/'+rid).json()
    again=c.post('/api/runs/'+rid+'/rerun',json={})
    assert again.status_code==200 and again.json()['revision']==2
    after=c.get('/api/runs/'+rid).json()
    assert after['revision']==2 and after['status']=='已复核'
    assert after['balance']==before['balance'];assert after['energy_rows']==before['energy_rows']

def test_rerun_divergence_keeps_old_batch(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    a=c.post('/api/runs',json={'dataset':'heat'}).json();rid=a['manifest']['run_id']
    c.post('/api/runs/'+rid+'/review',json={'revision':1})
    before=c.get('/api/runs/'+rid).json()
    # 模拟规则版本变化导致源快照重算出不同批号。
    stale=evaluate({'dataset':'heat'})
    diverged={**stale,'manifest':{**stale['manifest'],'run_id':'diverged0000000','sha256':'diverged'}}
    monkeypatch.setattr(main,'evaluate',lambda payload,snapshot=None:diverged)
    r=c.post('/api/runs/'+rid+'/rerun',json={})
    assert r.status_code==409;assert r.json()['code']=='RERUN_DIVERGED'
    after=c.get('/api/runs/'+rid).json()
    assert after['revision']==2 and after['status']=='已复核'
    assert after['balance']==before['balance']
    assert len(c.get('/api/runs').json())==1

def test_switch_failure_does_not_create_batch(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    a=c.post('/api/runs',json={'dataset':'clean'}).json();rid=a['manifest']['run_id']
    before=len(c.get('/api/runs').json())
    bad=c.post('/api/runs',json={'dataset':'../bad'})
    assert bad.status_code==422
    assert len(c.get('/api/runs').json())==before
    page=c.get('/loss?run_id='+rid)
    assert page.status_code==200 and rid in page.text

def test_store_guard_direct(tmp_path):
    p=str(tmp_path/'guard.sqlite')
    good=evaluate({'dataset':'clean'});store.save(p,good)
    rid=good['manifest']['run_id']
    store.review(p,rid,1)
    other=dict(good);other['manifest']={**good['manifest'],'run_id':'other00000000000','sha256':'x'}
    with pytest.raises(BusinessError) as e:save_rerun(p,other,rid)
    assert e.value.code=='RERUN_DIVERGED'
    assert store.get(p,rid)['revision']==2

def test_pages_show_settled_and_pending(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'dataset':'heat'}).json();rid=run['manifest']['run_id']
    for key in ['loss','reconcile']:
        html=c.get('/'+key+'?run_id='+rid).text
        assert '可结算片段' in html and '待查片段' in html
        assert '温度依据' in html
