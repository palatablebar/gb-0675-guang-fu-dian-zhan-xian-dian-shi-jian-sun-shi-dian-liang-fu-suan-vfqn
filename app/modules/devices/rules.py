from app.coerce import number,instant
from app.config import SENTINELS
from app.errors import BusinessError
from app.reasons import reject
from collections import defaultdict
from app.temperature import TEMP_MIN, TEMP_MAX

def read_temperature(row):
    """温度是可选证据列：缺失/哨兵记为无证据，格式错误或越界标记原因码，不影响功率采信。"""
    raw = row.get('temperature')
    text = '' if raw is None else str(raw).strip()
    for unit in ('°C', '℃', 'C'):
        if text.endswith(unit):
            text = text[:-len(unit)].strip(); break
    if text in SENTINELS or text.upper() in SENTINELS:
        return None, None
    try:
        value = number(text)
    except BusinessError:
        return None, 'BAD_TEMPERATURE'
    if not TEMP_MIN <= value <= TEMP_MAX:
        return None, 'TEMPERATURE_RANGE'
    return value, None

def normalize(row,context):
    if row['device'] not in context['devices']:raise BusinessError('UNKNOWN_DEVICE','设备不存在')
    device=context['devices'][row['device']]
    if row['kind']=='EVENT':
        start,end=instant(row['start']),instant(row['end'])
        if not start<end or (end-start).total_seconds()>86400:raise BusinessError('INVALID_RANGE','限电区间无效或超过24小时')
        return {**row,'start':start.isoformat(),'end':end.isoformat(),'zone':device['zone'],'temp_c':None,'temp_code':None}
    if row['kind']!='TELEMETRY':raise BusinessError('BAD_KIND','记录类型无效')
    factor={'kW':1,'MW':1000}.get(row['unit'])
    if factor is None:raise BusinessError('BAD_UNIT','功率单位无效')
    power=number(row['power'])*factor;irr=number(row['irradiance'])
    if power<0 or irr<0 or power>number(device['capacity_kw'])*number('1.2'):raise BusinessError('INVALID_RANGE','功率或辐照度超出范围')
    if row['status'] not in {'健康','故障'}:raise BusinessError('BAD_STATUS','设备状态无效')
    temp,temp_code=read_temperature(row)
    return {**row,'at':instant(row['at']).isoformat(),'power_kw':str(power),'irradiance':str(irr),'zone':device['zone'],
            'temp_c':None if temp is None else str(temp),'temp_code':temp_code}
def apply(state):
    good=[]
    for row in state['records']:
        try:good.append(normalize(row,state['context']))
        except BusinessError as e:reject(state,row,e.code,e.message)
    grouped=defaultdict(list)
    for row in good:
        if row['kind']=='TELEMETRY':grouped[(row['device'],row['at'])].append(row)
    bad_ids=set()
    for rows in grouped.values():
        if len(rows)>1:
            identities={(r['power_kw'],r['irradiance'],r['status'],r.get('temp_c'),r.get('temp_code')) for r in rows}
            victims=rows if len(identities)>1 else sorted(rows,key=lambda r:r['id'])[1:]
            for row in victims:reject(state,row,'SAMPLE_CONFLICT' if len(identities)>1 else 'SAMPLE_DUPLICATE','设备同一时刻存在重复观测');bad_ids.add(row['id'])
    state['records']=[r for r in good if r['id'] not in bad_ids]
    state['views']['devices']={'title':'设备与单位','rows':state['records'],'metrics':{'设备':len(state['context']['devices']),'规范记录':len(state['records']),'隔离':len(state['quarantine'])}}
