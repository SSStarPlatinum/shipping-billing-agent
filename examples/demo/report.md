# 航运智能账单助手 · 运行报告

## 1. 概览

| 指标 | 数值 |
|---|---|
| 记账主体 | NEPTUNE SHIPPING LIMITED |
| OCR 模式 | auto |
| LLM 增强 | 关闭（纯规则，确定性） |
| 处理文件数 | 4 |
| 解析单据数 | 4 |
| 输出记录数 | 7 |
| 总耗时 | 0.36s |
| 新增账单 | 1 |
| 更新账单 | 1 |
| 无需变更 | 2 |
| 转人工复核 | 3 |
| AR（应收） | 4 |
| AP（应付） | 0 |
| 方向待定 | 3 |

## 2. 输出记录明细

| # | 操作 | 单据类型 | 单据编号 | 日期 | 签发方 → 接收方 | 费用项目 | 金额 | 币种 | 付款 | AR/AP | 置信度 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | UPDATE | Debit Note | DC-108478 | 2026-10-24 | NEPTUNE SHIPPING LIMITED → - | Handling Charge | 65.00 | USD | COD | AR | 0.82 |
| 2 | CREATE | Debit Note | DC-108478 | 2026-10-24 | NEPTUNE SHIPPING LIMITED → - | Detention Charge | 1,310.40 | USD | COD | AR | 0.80 |
| 3 | NO_CHANGE | Debit Note | DC-108478 | 2026-10-24 | NEPTUNE SHIPPING LIMITED → - | Handling Charge | 35.00 | USD | COD | AR | 0.82 |
| 4 | NO_CHANGE | Arrival Notice | CMDUCHN4053601 | - | NEPTUNE SHIPPING LIMITED → CIMC WETRANS INTL LOG CO LTD | On Carriage Charge | 245.00 | USD | - | AR | 0.91 |
| 5 | PENDING_REVIEW | Invoice | ORFR1618731 | 2025-10-03 | ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC → ALL AMERICA FORWARDING, INC | Wharfage Administration Fee | 5.00 | USD | - | - | 0.82 |
| 6 | PENDING_REVIEW | Invoice | ORFR1618731 | 2025-10-03 | ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC → ALL AMERICA FORWARDING, INC | Wharfage | 21.90 | USD | - | - | 0.82 |
| 7 | PENDING_REVIEW | Pre-Alert | OOLU4055331281 | 2026-10-20 | luka@pltplt.com → li | Handling Charge | 50.00 | USD | PP | - | 0.81 |

## 3. 处理依据（审计链）

### 记录 1：Handling Charge · 65.00 USD · UPDATE

- 来源文件：`PDF 示例账单2.pdf`（pdf，line 26 (section header line 25)，抽取方式 rules，OCR pdf_text_layer）
- 命中台账：BILL-1001（单据编号+费用名称）
- 从 PDF 示例账单2.pdf（pdf，line 26 (section header line 25)）解析出费用：HANDLING CHARGE，来源 pdf_text_layer
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『NEPTUNE SHIPPING LIMITED』相似度 1.00，与接收方『未知』相似度 0.00
- 我方是单据的签发方（对外开票/收费），基础方向判定为 AR
- 最终过账方向：AR
- 按单据编号 DC-108478 + 费用名称 Handling Charge 匹配到台账 BILL-1001
- 同编号同费用名共有 2 条历史明细，按金额最接近原则选中（差额 25.00）
- 金额存在差异：台账 40.0 / 单据 65.0
- 匹配到已有账单且输入材料明确变更了字段，按增量更新处理
- 字段 amount 由 40.0 更新为 65.0（输入材料金额与台账不一致）
- 其他字段未被输入材料明确要求修改，保留台账原值
- ⚠️ 提示：交易双方信息不完整

### 记录 2：Detention Charge · 1,310.40 USD · CREATE

- 来源文件：`PDF 示例账单2.pdf`（pdf，line 27 (section header line 25)，抽取方式 rules，OCR pdf_text_layer）
- 从 PDF 示例账单2.pdf（pdf，line 27 (section header line 25)）解析出费用：ADD'L DETENTION FREE TIME CHARGE，来源 pdf_text_layer
- 费用名称标准化：'ADD'L DETENTION FREE TIME CHARGE' → 'Detention Charge'
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『NEPTUNE SHIPPING LIMITED』相似度 1.00，与接收方『未知』相似度 0.00
- 我方是单据的签发方（对外开票/收费），基础方向判定为 AR
- 最终过账方向：AR
- 四个匹配规则均未命中，判定为新账单
- 未匹配到已有账单，按新增处理
- ⚠️ 提示：交易双方信息不完整

### 记录 3：Handling Charge · 35.00 USD · NO_CHANGE

- 来源文件：`PDF 示例账单2.pdf`（pdf，line 28 (section header line 25)，抽取方式 rules，OCR pdf_text_layer）
- 命中台账：BILL-1002（单据编号+费用名称）
- 从 PDF 示例账单2.pdf（pdf，line 28 (section header line 25)）解析出费用：HANDLING CHARGE，来源 pdf_text_layer
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『NEPTUNE SHIPPING LIMITED』相似度 1.00，与接收方『未知』相似度 0.00
- 我方是单据的签发方（对外开票/收费），基础方向判定为 AR
- 最终过账方向：AR
- 按单据编号 DC-108478 + 费用名称 Handling Charge 匹配到台账 BILL-1002
- 同编号同费用名共有 2 条历史明细，按金额最接近原则选中（差额 0.00）
- 匹配到已有账单且所有字段一致，无需变更
- 台账字段与输入材料一致，未做任何覆盖
- ⚠️ 提示：交易双方信息不完整

### 记录 4：On Carriage Charge · 245.00 USD · NO_CHANGE

- 来源文件：`PDF示例账单1.pdf`（pdf，line 14 (section header line 13)，抽取方式 rules，OCR pdf_text_layer）
- 命中台账：BILL-2002（提单号+费用名称+币种）
- 从 PDF示例账单1.pdf（pdf，line 14 (section header line 13)）解析出费用：On Carriage Additional，来源 pdf_text_layer
- 费用名称标准化：'On Carriage Additional' → 'On Carriage Charge'
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『NEPTUNE SHIPPING LIMITED』相似度 1.00，与接收方『CIMC WETRANS INTL LOG CO LTD』相似度 0.10
- 我方是单据的签发方（对外开票/收费），基础方向判定为 AR
- 最终过账方向：AR
- 按提单号 CMDUCHN4053601 + 费用名称 + 币种匹配到台账 BILL-2002
- 匹配到已有账单，金额/币种/双方一致；仅补全台账空字段，不构成更新
- 台账字段 reference_no 为空，按输入材料补全为 CMDUCHN4053601（补全，不构成覆盖）
- 其他字段未被输入材料明确要求修改，保留台账原值

### 记录 5：Wharfage Administration Fee · 5.00 USD · PENDING_REVIEW

- 来源文件：`图片示例.png`（image，line 35 (section header line 34)，抽取方式 rules，OCR replay_cache）
- 从 图片示例.png（image，line 35 (section header line 34)）解析出费用：WHARFAGE ADMINISTRATION FEE，来源 replay_cache
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC』相似度 0.18，与接收方『ALL AMERICA FORWARDING, INC』相似度 0.07
- 无法从单据确定我方角色，按规范不允许仅凭单据类型猜测，转人工复核
- 四个匹配规则均未命中，判定为新账单
- 无法结合记账主体判断应收应付，转人工复核

### 记录 6：Wharfage · 21.90 USD · PENDING_REVIEW

- 来源文件：`图片示例.png`（image，line 36 (section header line 34)，抽取方式 rules，OCR replay_cache）
- 从 图片示例.png（image，line 36 (section header line 34)）解析出费用：WHARFAGE，来源 replay_cache
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC』相似度 0.18，与接收方『ALL AMERICA FORWARDING, INC』相似度 0.07
- 无法从单据确定我方角色，按规范不允许仅凭单据类型猜测，转人工复核
- 四个匹配规则均未命中，判定为新账单
- 无法结合记账主体判断应收应付，转人工复核

### 记录 7：Handling Charge · 50.00 USD · PENDING_REVIEW

- 来源文件：`深圳容宇 PRE-ALERT SSZSE26093128 VASINGER LLC 1x40HQ  CNTR NO._ TIIU8975975   MBL NO._ OOLU4055331281  Arrived time 2026-10-30.eml`（eml，line 14，抽取方式 rules，OCR 未使用）
- 命中台账：BILL-2001（提单号+费用名称+币种）
- 从 深圳容宇 PRE-ALERT SSZSE26093128 VASINGER LLC 1x40HQ  CNTR NO._ TIIU8975975   MBL NO._ OOLU4055331281  Arrived time 2026-10-30.eml（eml，line 14）解析出费用：H/C
- 费用名称标准化：'H/C' → 'Handling Charge'
- 记账主体『NEPTUNE SHIPPING LIMITED』与签发方『luka@pltplt.com』相似度 0.17，与接收方『li』相似度 0.00
- 无法从单据确定我方角色，按规范不允许仅凭单据类型猜测，转人工复核
- 按提单号 OOLU4055331281 + 费用名称 + 币种匹配到台账 BILL-2001
- 无法结合记账主体判断应收应付，转人工复核
- 字段 recipient 由 PLTPLT 更新为 li（输入材料接收方与台账不一致）
- 台账字段 reference_no 为空，按输入材料补全为 OOLU4055331281（补全，不构成覆盖）
- 若人工确认无误，将按上述变更写回台账

## 4. 单据解析情况

| 文件 | 类型 | 单据类型 | 费用条数 | 明细合计 | 单据总计 | OCR 来源 |
|---|---|---|---|---|---|---|
| PDF 示例账单2.pdf | pdf | Debit Note | 3 | 1,410.40 | 1,410.40 | pdf_text_layer |
| PDF示例账单1.pdf | pdf | Arrival Notice | 1 | 245.00 | 245.00 | pdf_text_layer |
| 图片示例.png | image | Invoice | 2 | 26.90 | 26.90 | replay_cache |
| 深圳容宇 PRE-ALERT SSZSE26093128 VASINGER LLC 1x40HQ  CNTR NO._ TIIU8975975   MBL NO._ OOLU4055331281  Arrived time 2026-10-30.eml | eml | Pre-Alert | 1 | 50.00 | null | - |

## 5. 校验统计

- 错误：0
- 警告：3
- 提示：3

## 6. 逐文件运行日志

| 文件 | 状态 | 耗时(ms) | 记录数 | 错误 |
|---|---|---|---|---|
| PDF 示例账单2.pdf | ok | 176 | 3 | - |
| PDF示例账单1.pdf | ok | 57 | 1 | - |
| 图片示例.png | ok | 16 | 2 | - |
| 深圳容宇 PRE-ALERT SSZSE26093128 VASINGER LLC 1x40HQ  CNTR NO._ TIIU8975975   MBL NO._ OOLU4055331281  Arrived time 2026-10-30.eml | ok | 112 | 1 | - |

## 7. 评测指标

```json
{
  "total_expected": 7,
  "matched": 7,
  "recall": 1.0,
  "precision": 1.0,
  "operation_accuracy": 1.0,
  "posting_accuracy": 1.0,
  "field_accuracy": {
    "operation": 1.0,
    "posting_type": 1.0,
    "reference_no": 1.0,
    "currency": 1.0,
    "document_date": 1.0
  },
  "mismatches": [],
  "unexpected": []
}
```

---

> 本报告由 `python -m shipping_billing` 自动生成。所有记录均由确定性规则产出，可复现。
