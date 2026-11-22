from decimal import Decimal
from app.engine import evaluate
from copy import deepcopy

def heat(overrides=None):
    return evaluate({'dataset':'heat','overrides':overrides or {}})

def test_segment_temperature_corrects_real():
    s=heat()
    a=next(r for r in s['energy_rows'] if r['device']=='INV-A' and r['start'].endswith('02:00:00+00:00'))
    # 35°C：系数 0.96，基线 100→96，实际 60，10 分钟损失 6kWh。
    assert a['temp_state']=='applied'
    assert Decimal(a['adjusted_baseline_kw'])==Decimal('96')
    assert Decimal(a['loss_kwh']).quantize(Decimal('.0001'))==Decimal('6.0000')

def test_hotter_segment_reduces_baseline_more():
    s=heat()
    rows=[r for r in s['energy_rows'] if r['device']=='INV-A' and r['temp_state']=='applied']
    assert Decimal(rows[1]['adjusted_baseline_kw'])<Decimal(rows[0]['adjusted_baseline_kw'])

def test_temperature_missing_is_pending_not_zero():
    s=heat()
    row=next(r for r in s['energy_rows'] if r['device']=='INV-A' and r['start'].endswith('02:20:00+00:00'))
    assert row['temp_state']=='pending';assert row['pending_reason']=='TEMP_MISSING'
    assert row['loss_kwh'] is None;assert row['adjusted_baseline_kw'] is None

def test_pending_blocks_archive():
    s=heat();assert s['archive']['status']=='待复核';assert s['archive']['temperature_pending']==1

def test_daily_max_is_proposal_only():
    s=heat({'temperature_correction':{'mode':'daily_max','daily_max_c':'70'}})
    assert s['balance']['loss_kwh']=='0.0000'
    assert s['balance']['temperature_pending']==len(s['energy_rows'])
    assert all(r['loss_kwh'] is None for r in s['energy_rows'])
    assert all(r.get('proposed_loss_kwh') is not None for r in s['energy_rows'] if r.get('raw_baseline_kw'))

def test_daily_max_missing_value_stays_pending():
    s=heat({'temperature_correction':{'mode':'daily_max'}})
    # 无 daily_max_c 时回退本批观测最大值，仍只能作建议，不能结算。
    assert s['temperature_plan']['daily_source']=='observed_max'
    assert all(r.get('pending_reason')=='DAILY_MAX_UNSETTLED' for r in s['energy_rows'] if r.get('raw_baseline_kw'))

def test_temperature_off_restores_raw_baseline():
    s=heat({'temperature_correction':{'mode':'off'}})
    assert s['balance']['temp_adjust_kwh']=='0.0000'
    assert s['balance']['loss_kwh']=='26.6667'
    assert s['balance']['unknown_segments']==0

def test_temperature_balance_identity():
    s=heat();b=s['balance']
    assert b['temp_balanced'] is True
    assert Decimal(b['raw_baseline_kwh'])==Decimal(b['baseline_kwh'])+Decimal(b['temp_adjust_kwh'])

def test_no_temperature_batch_unchanged():
    s=evaluate({'dataset':'clean'})
    assert s['temperature_plan']['enabled'] is False
    assert s['balance']['loss_kwh']=='26.6667'

def test_temperature_url_segments_cannot_borrow_neighbour_sample():
    from app.modules.baseline.rules import estimate
    from app.temperature import plan, correct
    s=heat()
    seg=dict(s['segments'][0],temp_c='35')
    # 把片段起点挪到样本有效期之外：温度证据不覆盖，片段待查。
    shifted=dict(seg,start='2026-10-15T03:00:00+00:00',end='2026-10-15T03:10:00+00:00')
    row=correct(estimate(shifted,s['records'],s['context']),plan(s),s['context'])
    assert row['temp_state']=='pending';assert row['pending_reason']=='TEMP_UNCOVERED'

def test_bad_temperature_value_not_evidence():
    # 通过完整管线验证：温度值无法解析时，该片段温度证据不可采信，片段待查。
    raw=[r.split(',') for r in open('sample_data/heat.csv').read().splitlines()]
    for cells in raw:
        if cells[0]=='INV-A-10': cells[-1]='oops'
    text='\n'.join(','.join(c) for c in raw)
    s=evaluate({'csv_text':text})
    row=next(r for r in s['energy_rows'] if r.get('temp_sample_id')=='INV-A-10')
    assert row['temp_state']=='pending';assert row['pending_reason']=='TEMP_INVALID'
    assert row['loss_kwh'] is None

def test_evidence_ids_link_event_baseline_temperature():
    s=heat();row=next(r for r in s['energy_rows'] if r['temp_state']=='applied')
    assert row['event_ids'] and row['reference_ids'] and row['temp_sample_id']==row['sample_id']

def test_observed_temperature_bounds():
    s=heat();assert s['temperature_plan']['observed_min']=='32';assert s['temperature_plan']['observed_max']=='58'
