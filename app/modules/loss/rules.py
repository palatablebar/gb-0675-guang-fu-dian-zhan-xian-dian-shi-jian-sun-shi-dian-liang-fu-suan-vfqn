from decimal import Decimal,ROUND_HALF_UP
from app.coerce import number

def integrate(estimate):
    # 温度待查片段：不填0、也不回退未修正基线，整段保持未定。
    if estimate.get('temp_state')=='pending':
        return {**estimate,'loss_kwh':None,'actual_kwh':None,'baseline_kwh':None,'raw_baseline_kwh':None,'temp_adjust_kwh':None}
    if estimate['baseline_kw'] is None:return {**estimate,'loss_kwh':None,'actual_kwh':None,'baseline_kwh':None}
    hours=Decimal(estimate['seconds'])/3600;actual=number(estimate['actual_kw']);baseline=number(estimate['baseline_kw'])
    # 修正后基线用于损失；未修正基线单列，供对账解释温度扣减。
    adjusted=number(estimate['adjusted_baseline_kw']) if estimate.get('adjusted_baseline_kw') is not None else baseline
    loss=max(Decimal(0),adjusted-actual)*hours
    # 保留计算精度，汇总后再统一保留四位，避免逐段量化制造尾差。
    return {**estimate,'loss_kwh':str(loss),'actual_kwh':str(actual*hours),'baseline_kwh':str((actual+max(Decimal(0),adjusted-actual))*hours),
            'raw_baseline_kw':str(baseline),'raw_baseline_kwh':str(baseline*hours),'temp_adjust_kwh':str((baseline-adjusted)*hours),'code':'LOSS' if loss else 'NO_LOSS'}

def apply(state):
    rows=[integrate(e) for e in state['estimates']];state['energy_rows']=rows
    total=sum((number(r['loss_kwh']) for r in rows if r['loss_kwh'] is not None),Decimal(0))
    state['views']['loss']={'title':'损失电量','rows':[{k:v for k,v in r.items() if k not in ['sample_at','zone','irradiance','status']} for r in rows],'metrics':{'已确定损失_kWh':str(total.quantize(Decimal('.0001'))),'未定片段':sum(r['loss_kwh'] is None for r in rows)}}
