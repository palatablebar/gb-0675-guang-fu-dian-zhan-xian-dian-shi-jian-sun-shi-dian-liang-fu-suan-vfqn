"""逐片段温度修正。

裁定见 docs/RULING-TEMPERATURE.md：温度只能以片段自己的遥测证据进入损失单；
日最高温统一扣减只作为待查片段上的建议值，不参与结算。
"""
from decimal import Decimal
from datetime import timedelta

from app.coerce import number, instant
from app.errors import BusinessError

DEFAULTS = {'mode': 'segment', 'reference_c': '25', 'coefficient_per_c': '0.004'}
MODES = {'off', 'segment', 'daily_max'}
# 组件温度合理区间，超出视为不可采信证据而不是物理事实。
TEMP_MIN = Decimal('-50')
TEMP_MAX = Decimal('150')


def _quant(value, places='0.000001'):
    return format(Decimal(value).quantize(Decimal(places)), 'f')


def settings(context):
    raw = context.get('temperature_correction') or {}
    if not isinstance(raw, dict):
        raise BusinessError('BAD_TEMPERATURE_CONTEXT', '温度修正参数须为对象')
    cfg = {**DEFAULTS, **raw}
    mode = str(cfg.get('mode', 'segment'))
    if mode not in MODES:
        raise BusinessError('BAD_TEMPERATURE_MODE', '温度修正口径无效')
    try:
        reference = number(cfg['reference_c'])
        coefficient = number(cfg['coefficient_per_c'])
    except BusinessError:
        raise BusinessError('BAD_TEMPERATURE_CONTEXT', '温度修正参数格式错误')
    if reference < TEMP_MIN or reference > TEMP_MAX:
        raise BusinessError('BAD_TEMPERATURE_CONTEXT', '温度参考值超出范围')
    if coefficient < 0 or coefficient > 1:
        raise BusinessError('BAD_TEMPERATURE_CONTEXT', '温度系数须在0到1之间')
    daily = None
    if cfg.get('daily_max_c') not in (None, ''):
        try:
            daily = number(cfg['daily_max_c'])
        except BusinessError:
            raise BusinessError('BAD_TEMPERATURE_CONTEXT', '日最高温格式错误')
        if daily < TEMP_MIN or daily > TEMP_MAX:
            raise BusinessError('BAD_TEMPERATURE_CONTEXT', '日最高温超出范围')
    return {'mode': mode, 'reference_c': reference, 'coefficient_per_c': coefficient, 'daily_max_c': daily}


def plan(state):
    """判定本批是否启用温度修正，并给出批次级参数。

    启用条件：批次带温度列，或上下文显式声明温度修正口径。
    只带温度列但整列为空时仍然启用——否则会悄悄退回未修正基线，正是复核要防的。
    完全没有温度证据的既有批次行为不变。
    """
    cfg = settings(state['context'])
    declared = bool(state.get('has_temperature')) or 'temperature_correction' in state['context']
    observed = [number(r['temp_c']) for r in state['records']
                if r.get('kind') == 'TELEMETRY' and r.get('temp_c') is not None and not r.get('temp_code')]
    result = {**cfg, 'enabled': False, 'declared': declared, 'observed_min': None, 'observed_max': None,
              'evidence_rows': len(observed), 'daily_source': None}
    if observed:
        result['observed_min'] = str(min(observed))
        result['observed_max'] = str(max(observed))
    if cfg['mode'] == 'off' or not declared:
        return result
    if cfg['mode'] == 'segment':
        result['enabled'] = True
        return result
    daily = cfg['daily_max_c']
    if daily is not None:
        result['daily_source'] = 'context'
    elif observed:
        daily = max(observed)
        result['daily_max_c'] = daily
        result['daily_source'] = 'observed_max'
    # 显式要求日最高温却拿不到值：保持启用，片段全部待查而不是回退未修正基线。
    result['enabled'] = True
    if daily is None:
        result['daily_source'] = 'missing'
    return result


def _factor(temp, reference, coefficient):
    excess = max(Decimal(0), temp - reference)
    return max(Decimal(0), min(Decimal(1), Decimal(1) - coefficient * excess))


def _evidence_problem(estimate, temp, context):
    if estimate.get('temp_code') in {'BAD_TEMPERATURE', 'TEMPERATURE_RANGE'}:
        return 'TEMP_INVALID'
    if temp is None:
        return 'TEMP_MISSING'
    if not estimate.get('sample_at') or not estimate.get('start') or not estimate.get('end'):
        return 'TEMP_NO_SAMPLE'
    start, end = instant(estimate['start']), instant(estimate['end'])
    at = instant(estimate['sample_at'])
    cadence = timedelta(seconds=int(context['cadence_seconds']))
    if not (at <= start and end <= at + cadence):
        return 'TEMP_UNCOVERED'
    return ''


def _row_evidence(out, estimate, temp):
    out['temp_c'] = str(temp) if temp is not None else None
    out['temp_sample_id'] = estimate.get('sample_id') or None
    out['temp_sample_at'] = estimate.get('sample_at') or None
    return out


def correct(estimate, plan_, context):
    """在基线估计上叠加本片段的温度证据，返回带 temp_state 的损失输入。"""
    out = dict(estimate)
    # 未声明温度的批次不插入温度字段，保持原有损失单列结构。
    if not plan_['enabled']:
        if plan_.get('declared'):
            out['temp_state'] = 'off'
            out.setdefault('pending_reason', None)
        return out
    out['temp_state'] = 'off'
    # 统一字段，避免表格按首行取列时丢失待查原因。
    out.setdefault('pending_reason', None)
    raw = estimate.get('baseline_kw')
    temp = None
    if estimate.get('temp_c') is not None:
        try:
            temp = number(estimate['temp_c'])
        except BusinessError:
            out['temp_code'] = 'BAD_TEMPERATURE'
    _row_evidence(out, estimate, temp)
    out['temp_reference_c'] = str(plan_['reference_c'])
    if raw is None:
        # 上游无基线/无实际值，保留原待查原因，不制造温度结论。
        out['temp_state']='na'
        return out
    out['raw_baseline_kw'] = str(number(raw))
    actual = number(estimate['actual_kw']) if estimate.get('actual_kw') is not None else None
    hours = Decimal(estimate['seconds']) / 3600
    if plan_['mode'] == 'daily_max':
        daily = plan_.get('daily_max_c')
        if daily is None:
            out.update(temp_state='pending', code='TEMP_PENDING', pending_reason='DAILY_MAX_MISSING',
                       adjusted_baseline_kw=None, temp_factor=None, temp_adjust_kw=None)
            return out
        factor = _factor(daily, plan_['reference_c'], plan_['coefficient_per_c'])
        proposed = number(raw) * factor
        out.update(temp_state='pending', code='TEMP_PENDING', pending_reason='DAILY_MAX_UNSETTLED',
                   temp_used_c=str(daily), temp_factor=_quant(factor),
                   proposed_baseline_kw=str(proposed), proposed_adjust_kw=str(number(raw) - proposed),
                   proposed_loss_kwh=str(max(Decimal(0), proposed - actual) * hours) if actual is not None else None)
        return out
    problem = _evidence_problem(estimate, temp, context)
    if problem:
        out.update(temp_state='pending', code='TEMP_PENDING', pending_reason=problem,
                   adjusted_baseline_kw=None, temp_factor=None, temp_adjust_kw=None)
        return out
    factor = _factor(temp, plan_['reference_c'], plan_['coefficient_per_c'])
    adjusted = number(raw) * factor
    out.update(temp_state='applied', adjusted_baseline_kw=str(adjusted), temp_factor=_quant(factor),
               temp_adjust_kw=str(number(raw) - adjusted))
    return out
