# 裁定：温度修正如何进入损失单

## 争议

夏季组件温升会让逆变器在**未限电**时也降额。值班希望“按当天最高温统一扣减基线”，以便尽快得到限电损失；复核要求每个损失片段能追到**对应时段**的温度依据。两者不能同时成立：日最高温是一个批次级标量，无法证明某个 10 分钟片段当时的温度，统一扣减会把高温降额记成限电。

## 裁定

1. **温度修正只能按片段进入损失单，且必须绑定该片段自己的温度证据。**
   - 证据来源：该片段所用遥测样本上携带的温度值（`temperature` 列，°C）。
   - 证据有效窗口与功率一致：自采样时刻起 600 秒左值保持，不跨窗外推；片段的半开区间 `[start,end)` 必须整体落在 `[sample_at, sample_at+cadence)` 内。
   - 损失行同时保留 `event_ids`（限电事件）、`baseline_kw` 与 `reference_ids`（采用基线）、`temp_c`、`temp_sample_id`、`temp_sample_at`、`temp_factor`、`temp_adjust_kw`、`adjusted_baseline_kw`（修正量），三者可互相解释。

2. **日最高温统一扣减不是可结算依据。**
   - 上下文 `temperature_correction.mode = "daily_max"` 时，系统只给出每片段的“建议扣减”（`proposed_adjust_kw`）供值班参考，所有片段标记为**待查**，`loss_kwh` 保持 `null`，不进入确定损失、不参与能量对账、不参与归档。
   - 要结算，必须切回 `mode = "segment"` 并提供逐片段温度证据。

3. **证据不足的片段待查，不填 0，也不回退到未修正基线。**
   - 缺失/哨兵温度、温度样本未覆盖片段、日最高温模式、策略参数不全，均产生 `pending_reason`，`loss_kwh` 为 `null`。
   - 待查片段计入“未定片段”，阻止归档。

4. **修正公式（逐片段，Decimal）：**

   ```
   excess   = max(0, T_segment - reference_c)
   factor   = clamp(1 - coefficient_per_c * excess, 0, 1)
   adjusted = baseline_kw * factor
   temp_adjust_kw = baseline_kw - adjusted
   loss_kwh = max(0, adjusted - actual_kw) * seconds / 3600
   ```

   默认 `reference_c=25`、`coefficient_per_c=0.004`（0.4%/°C），可由上下文覆盖。

5. **能量账仍闭合，且温度单列：**

   ```
   raw_baseline_kwh = baseline_kw * h          （未修正基线）
   temp_adjust_kwh  = (baseline_kw - adjusted) * h
   baseline_kwh     = adjusted * h             （对账用基线）
   baseline_kwh     = actual_kwh + loss_kwh
   raw_baseline_kwh = baseline_kwh + temp_adjust_kwh
   ```

   对账新增 `temp_balanced`；`energy_balanced` 同时要求上述两条恒等式成立。

6. **无温度证据的批次行为不变。** 若整批遥测都没有温度值，温度修正自动不启用，原有基线/损失结果不变；一旦批次声明了温度证据，则每个片段都必须有覆盖该片段的温度证据，否则待查。

7. **已结算结果不可变。**
   - 同一输入 + 同一规则版本重跑得到相同批号，`INSERT OR IGNORE` 不新增、不覆盖。
   - 已复核批次的输入快照重算若得到不同批号（例如规则版本变化），返回 `409 RERUN_DIVERGED`，保留旧版，不新增批次。
   - 切换样例失败时不写入任何新批次，当前页面与旧批次保持可读。

## 样例

`sample_data/heat.csv` 为带温度列的高温批：

| 片段 | 温度 | 原始基线 | 修正基线 | 损失 |
|---|---|---|---|---|
| INV-A 10:00–10:10 | 35°C | 100 kW | 96 kW | 6.0000 kWh |
| INV-A 10:10–10:20 | 55°C | 100 kW | 88 kW | 4.6667 kWh |
| INV-A 10:20–10:30 | 缺测 | 100 kW | — | 待查 |
| INV-B 10:00–10:10 | 35°C | 50 kW | 48 kW | 3.0000 kWh |
| INV-B 10:10–10:20 | 55°C | 50 kW | 44 kW | 2.3333 kWh |

确定损失 16.0000 kWh，温度扣减 4.0000 kWh，待查 1 片段 / 10 分钟。若改用日最高温 70°C 统一扣减，4 个可结算片段只剩 11.0000 kWh，且缺失温度的片段同样被“算平”，这正是复核拒绝的算法。
