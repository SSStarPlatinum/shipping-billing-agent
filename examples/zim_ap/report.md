# 航运智能账单助手 · 运行报告

## 1. 概览

| 指标 | 数值 |
|---|---|
| 记账主体 | ALL AMERICA FORWARDING, INC. |
| OCR 模式 | auto |
| LLM 增强 | 关闭（纯规则，确定性） |
| 处理文件数 | 1 |
| 解析单据数 | 1 |
| 输出记录数 | 2 |
| 总耗时 | 0.06s |
| 新增账单 | 0 |
| 更新账单 | 0 |
| 无需变更 | 2 |
| 转人工复核 | 0 |
| AR（应收） | 0 |
| AP（应付） | 2 |
| 方向待定 | 0 |

## 2. 输出记录明细

| # | 操作 | 单据类型 | 单据编号 | 日期 | 签发方 → 接收方 | 费用项目 | 金额 | 币种 | 付款 | AR/AP | 置信度 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | NO_CHANGE | Invoice | ORFR1618731 | 2025-10-03 | ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC → ALL AMERICA FORWARDING, INC | Wharfage Administration Fee | 5.00 | USD | - | AP | 0.96 |
| 2 | NO_CHANGE | Invoice | ORFR1618731 | 2025-10-03 | ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC → ALL AMERICA FORWARDING, INC | Wharfage | 21.90 | USD | - | AP | 0.96 |

## 3. 处理依据（审计链）

### 记录 1：Wharfage Administration Fee · 5.00 USD · NO_CHANGE

- 来源文件：`图片示例.png`（image，line 35 (section header line 34)，抽取方式 rules，OCR replay_cache）
- 命中台账：BILL-3001（订舱号+费用名称+币种）
- 从 图片示例.png（image，line 35 (section header line 34)）解析出费用：WHARFAGE ADMINISTRATION FEE，来源 replay_cache
- 记账主体『ALL AMERICA FORWARDING, INC.』与签发方『ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC』相似度 0.40，与接收方『ALL AMERICA FORWARDING, INC』相似度 1.00
- 我方是单据的接收方（被开票/被收费），基础方向判定为 AP
- 最终过账方向：AP
- 按订舱号 ZIMUNGB1312144 匹配到台账 BILL-3001
- 匹配到已有账单，金额/币种/双方一致；仅补全台账空字段，不构成更新
- 台账字段 reference_no 为空，按输入材料补全为 ORFR1618731（补全，不构成覆盖）
- 其他字段未被输入材料明确要求修改，保留台账原值

### 记录 2：Wharfage · 21.90 USD · NO_CHANGE

- 来源文件：`图片示例.png`（image，line 36 (section header line 34)，抽取方式 rules，OCR replay_cache）
- 命中台账：BILL-3002（箱号+交易双方+日期+金额）
- 从 图片示例.png（image，line 36 (section header line 34)）解析出费用：WHARFAGE，来源 replay_cache
- 记账主体『ALL AMERICA FORWARDING, INC.』与签发方『ZIM AMERICAN INTEGRATED SHIPPING SERVICES CO., LLC』相似度 0.40，与接收方『ALL AMERICA FORWARDING, INC』相似度 1.00
- 我方是单据的接收方（被开票/被收费），基础方向判定为 AP
- 最终过账方向：AP
- 按箱号 JKU0450463 + 交易双方 + 金额匹配到台账 BILL-3002
- 匹配到已有账单，金额/币种/双方一致；仅补全台账空字段，不构成更新
- 台账字段 reference_no 为空，按输入材料补全为 ORFR1618731（补全，不构成覆盖）
- 其他字段未被输入材料明确要求修改，保留台账原值

## 4. 单据解析情况

| 文件 | 类型 | 单据类型 | 费用条数 | 明细合计 | 单据总计 | OCR 来源 |
|---|---|---|---|---|---|---|
| 图片示例.png | image | Invoice | 2 | 26.90 | 26.90 | replay_cache |

## 5. 校验统计

- 错误：0
- 警告：0
- 提示：2

## 6. 逐文件运行日志

| 文件 | 状态 | 耗时(ms) | 记录数 | 错误 |
|---|---|---|---|---|
| 图片示例.png | ok | 52 | 2 | - |

---

> 本报告由 `python -m shipping_billing` 自动生成。所有记录均由确定性规则产出，可复现。
