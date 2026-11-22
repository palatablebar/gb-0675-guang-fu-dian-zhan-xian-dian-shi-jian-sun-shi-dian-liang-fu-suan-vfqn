from decimal import Decimal
from app.coerce import number

def reconcile(state):
    known=[r for r in state['energy_rows'] if r['loss_kwh'] is not None];unknown=[r for r in state['energy_rows'] if r['loss_kwh'] is None]
    sums={k:sum((number(r[k]) for r in known),Decimal(0)) for k in ['loss_kwh','actual_kwh','baseline_kwh','raw_baseline_kwh','temp_adjust_kwh']}
    union_seconds=sum(r['seconds'] for r in state['event_unions']);slice_seconds=sum(r['seconds'] for r in state['energy_rows']);c=state['counts'];explained=len(state['records'])+len(state['quarantine'])+c['blank']+c['duplicate']+c['repeated_header']
    # Decimal周期小数允许最后一位精度误差，展示前统一量化到0.0001kWh再检查。
    quant=lambda v:v.quantize(Decimal('.0001'))
    energy_balanced=quant(sums['baseline_kwh'])==quant(sums['actual_kwh']+sums['loss_kwh'])
    # 温度单独成账：未修正基线 = 修正后基线 + 温度扣减，保证修正量可解释且不混入限电损失。
    temp_balanced=quant(sums['raw_baseline_kwh'])==quant(sums['baseline_kwh']+sums['temp_adjust_kwh'])
    return {'input':c['input'],'accepted':len(state['records']),'quarantined':len(state['quarantine']),'row_balanced':explained==c['input'],'union_seconds':union_seconds,'slice_seconds':slice_seconds,'time_balanced':union_seconds==slice_seconds,'loss_kwh':str(quant(sums['loss_kwh'])),'actual_kwh':str(quant(sums['actual_kwh'])),'baseline_kwh':str(quant(sums['baseline_kwh'])),'raw_baseline_kwh':str(quant(sums['raw_baseline_kwh'])),'temp_adjust_kwh':str(quant(sums['temp_adjust_kwh'])),'energy_balanced':energy_balanced and temp_balanced,'temp_balanced':temp_balanced,'unknown_segments':len(unknown),'unknown_seconds':sum(r['seconds'] for r in unknown),'temperature_pending':sum(r.get('temp_state')=='pending' for r in state['energy_rows']),'temperature_applied':sum(r.get('temp_state')=='applied' for r in state['energy_rows'])}
def apply(state):
    state['balance']=reconcile(state);state['views']['reconcile']={'title':'时间与能量四账','rows':[state['balance']],'metrics':{'确定损失_kWh':state['balance']['loss_kwh'],'未定分钟':state['balance']['unknown_seconds']//60,'并集分钟':state['balance']['union_seconds']//60,'温度扣减_kWh':state['balance']['temp_adjust_kwh']}}
