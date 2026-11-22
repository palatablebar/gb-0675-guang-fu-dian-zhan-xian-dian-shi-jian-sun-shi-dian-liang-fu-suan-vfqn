from decimal import Decimal
from app.coerce import number,instant
from app.temperature import plan as temperature_plan, correct as temperature_correct

def estimate(segment,records,context):
    base={**segment,'baseline_kw':None,'reference_ids':[]}
    if segment['actual_kw'] is None:return {**base,'code':'TARGET_GAP'}
    if segment['status']!='健康':return {**base,'code':'TARGET_FAULT'}
    target=context['devices'][segment['device']];width=number(context['irradiance_bin']);target_bin=number(segment['irradiance'])//width
    references=[]
    for row in records:
        if row['kind']!='TELEMETRY' or row['device']==segment['device']:continue
        device=context['devices'][row['device']]
        if not device['reference'] or device['zone']!=segment['zone'] or row['status']!='健康' or row['at']!=segment['sample_at']:continue
        if number(row['irradiance'])//width!=target_bin:continue
        if any(e['kind']=='EVENT' and e['device']==row['device'] and instant(e['start'])<=instant(row['at'])<instant(e['end']) for e in records):continue
        capacity=number(device['capacity_kw'])
        if capacity<=0:continue
        references.append((number(row['power_kw'])/capacity,row['id']))
    if len(references)<context['min_references']:return {**base,'reference_ids':[x[1] for x in references],'code':'NO_REFERENCE'}
    ratios=sorted(x[0] for x in references);mid=len(ratios)//2;ratio=ratios[mid] if len(ratios)%2 else (ratios[mid-1]+ratios[mid])/2
    baseline=min(number(target['capacity_kw']),ratio*number(target['capacity_kw']))
    return {**base,'baseline_kw':str(baseline),'reference_ids':sorted(x[1] for x in references),'code':'ESTIMATED'}

def apply(state):
    # 先拟合未修正基线，再逐片段叠加温度证据；温度不改变“采用哪组对照”。
    plan_=temperature_plan(state);state['temperature_plan']={k:(str(v) if isinstance(v,Decimal) else v) for k,v in plan_.items()}
    estimates=[temperature_correct(estimate(s,state['records'],state['context']),plan_,state['context']) for s in state['segments']]
    state['estimates']=estimates
    applied=sum(r.get('temp_state')=='applied' for r in estimates);pending=sum(r.get('temp_state')=='pending' for r in estimates)
    state['views']['baseline']={'title':'对照基线','rows':estimates,'metrics':{'可估计':sum(r['code']=='ESTIMATED' for r in estimates),'未定':sum(r['code']!='ESTIMATED' for r in estimates),'对照下限':state['context']['min_references'],'温度已修正':applied,'温度待查':pending}}
