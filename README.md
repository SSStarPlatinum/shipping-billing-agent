# 多格式文档解析的航运智能账单助手（AI AGENT）

> 用 Python + LangChain 实现的航运/货代账单智能体：把散落在 EML、PDF、扫描图片、Excel、Word 里的费用，
> 自动解析成**可入账、可审计、可复现**的标准 JSON，并完成"新增账单 / 增量更新 / 无需变更 / 转人工"的判定。

---

## 1. 30 秒跑起来

```bash
# 1) 安装依赖（Python >= 3.10）
pip install -r requirements.txt

# 2) 一键演示：跑 data/samples 下 4 个真实样例，输出到 output/demo
python -m shipping_billing demo

# 3) 与金标准对比，看完给指标
python -m shipping_billing eval
```

输出三个文件：

| 文件 | 内容 |
|---|---|
| `output/demo/results.json` | **标准输出**（题目"五、标准输出格式"要求的 JSON 数组） |
| `output/demo/report.md` | 人可读的运行报告 + 每条记录的处理依据（审计链） |
| `output/demo/ledger_after.json` | 处理后的台账（新增/更新后的账单） |
| `output/demo/run_log.json` | 逐文件耗时、OCR 来源、错误信息 |

`eval` 在样例上的实测结果：

```
total_expected 7   matched 7   recall 1.0   precision 1.0
operation_accuracy 1.0   posting_accuracy 1.0
field_accuracy {'operation':1.0,'posting_type':1.0,'reference_no':1.0,'currency':1.0,'document_date':1.0}
```

---

## 2. 它到底做了什么

以样例里的 `PDF 示例账单2.pdf`（一张 Debit Note）为例：

| 环节 | 系统行为 |
|---|---|
| 解析 | 读 PDF 文本层 → 定位表头 `DESCRIPTION UNIT RATE QTY P/C ...` → 逐行解析出 3 条明细 |
| 标准化 | `HANDLING CHARGE → Handling Charge`、`4 ADD'L DETENTION FREE TIME CHARGE → Detention Charge` |
| 校验 | 明细合计 65.00+1310.40+35.00 = 1410.40，与单据 `TOTAL AMOUNT 1,410.40` 一致 ✅ |
| 匹配 | 规则 1（单据编号 `DC-108478` + 费用名）命中台账 BILL-1001/BILL-1002；同名两条明细按**金额距离**区分 |
| 更新 | 65.00 那行：台账 40.00 → 更新为 65.00，**其余字段全部继承**；35.00 那行：完全一致 → `NO_CHANGE` |
| 新增 | 滞留费在台账里不存在 → `CREATE` |
| AR/AP | 记账主体 `NEPTUNE SHIPPING LIMITED` = 单据**签发方** → 我方对外收费 → `AR` |
| 审计 | 每条记录写明：来源文件/行号、匹配到哪张单、改了哪个字段、为什么改、其余字段为何保留 |

---

## 3. 目录结构

```
shipping_billing/
├── config.py             # 全部配置（OCR/LLM/记账主体/阈值），CLI > .env > 默认
├── models.py             # 三层模型：原始证据 → 语义 → 标准输出
├── pipeline.py           # 端到端编排（BillingAgent）
├── agent.py              # LangChain Runnable 图：规则 ∥ LLM → 融合 → 标准化
├── pipeline_utils.py     # 记录装配：匹配/方向/校验 → 标准 JSON + 变更集
├── ingest/               # ① 多格式解析
│   ├── dispatcher.py     #    按扩展名+魔数分发，附件递归（最大 5 层）
│   ├── eml_loader.py     #    邮件头/正文/HTML/附件（含内嵌签名图识别）
│   ├── pdf_loader.py     #    文本层优先，扫描件转 OCR
│   ├── image_loader.py   #    图片 → OCR
│   ├── excel_loader.py   #    xlsx/xlsm/xls（xlrd 可选）
│   ├── word_loader.py    #    docx；.doc 走 LibreOffice 或二进制兜底
│   └── ocr_client.py     #    OCR 网关：重试/超时/熔断/内容哈希缓存/自适应解析
├── extract/              # ② 语义抽取
│   ├── rows.py           #    费用行布局模板（8 种），OCR 混淆字符修复
│   ├── tables.py         #    表格列语义映射（中英文表头同义词）
│   ├── rules.py          #    规则抽取：类型/编号/日期/双方/币种/合计/标识/明细
│   ├── llm_extractor.py  #    LangChain LLM 抽取（可选，Pydantic 结构化 + 缓存 + 修复重试）
│   ├── merge.py          #    规则 vs LLM 融合：规则为准、LLM 补位、冲突降级
│   └── document_extractor.py  # 节点化编排 + 附件上下文合并
├── normalize/            # ③ 标准化：费用名（含题目 6 组映射）/ 币种 ISO4217 / PP-CC-COD
├── finance/posting.py    # ④ AR/AP 判定（主体视角 + 角色 + 方向反转）
├── matching/             # ⑤ 4 级匹配规则 + 台账增量写回
├── validation/           # ⑥ 校验（金额/币种/合计/OCR 混淆/重复）+ 置信度模型
└── harness/              # ⑦ 工程化：缓存、重试熔断、运行日志、报告、金标准评测
data/
├── samples/              # 题目提供的 4 个样例（EML / 2×PDF / PNG）
├── ledger/existing_bills.json   # 已有账单台账（6 行，覆盖 4 类匹配场景）
├── ocr_cache/            # OCR 结果回放（内容哈希命名）
└── expectations/golden.json     # 金标准用例
docs/                     # 设计文档
tests/                    # 89 个单测 + 端到端回归
scripts/ocr_smoke_test.py # 真实 OCR API 连通性自测
```

---

## 4. 核心设计

### 4.1 三层数据模型：证据 → 语义 → 输出

```
RawDocument(text/pages/tables/meta/attachments)   ← 一字不改的"证据"
        ↓ 抽取
ExtractedDocument(document_type/ref/date/parties/total/charges[+provenance])
        ↓ 匹配 · 方向 · 校验
OutputRecord(operation/document_info/transaction_party/charge_details/financial_attributes/audit_trail)
```

**所有格式最终都收敛成 `RawDocument`**，抽取层完全不关心来源是邮件还是扫描件——这是能持续加格式的前提。

### 4.2 抽取：确定性优先，LLM 补位

```
                 ┌── 规则抽取（金额/编号/日期/明细，可复现）──┐
RawDocument ─────┤                                          ├─→ 融合 → 标准化 → 校验
                 └── LLM 抽取（语义字段，可选，温度 0）──────┘
```

* **规则负责"硬事实"**：金额、币种、编号、日期、合计。这些字段一旦交给模型"大概其"，账就没法做了。
* **LLM 只负责"软语义"**：规则抓不到的签发方、模糊费用名、条款语气。
* **冲突处理**：数值以规则/原文字面值为准，LLM 结论只做交叉校验；冲突写入 `warnings` 并打折置信度。

费用行解析不靠"猜列"，而是 8 种**列布局模板**按信息量降序尝试，并用"表头区块定位"限制搜索范围
（例如 B/L 里 `SAPU2040689 C9950614 20ST 233PIECE(S) 43762 706.00` 这类行，绝不能被当成 706 美元的费用）。

### 4.3 AR / AP：不是看单据类型，是看"我是谁"

```
角色识别（我 = 签发方 or 接收方，公司名相似度）
    ├─ 我是签发方 → 我方收费 → AR
    └─ 我是接收方 → 我方被收费 → AP
再叠加方向修正：Credit Note（贷记）反转；红字行（负数）再反转
角色无法确定 → posting_type = null + PENDING_REVIEW（绝不猜）
```

样例里这就是关键：同一批文件，把 `--entity` 从 `NEPTUNE SHIPPING LIMITED` 换成
`ALL AMERICA FORWARDING, INC.`，ZIM 发票的结论立刻从"转人工"变成 `AP` 且命中台账。

### 4.4 匹配：4 级规则 + 金额判别 + 冲突上报

| 优先级 | 规则 | 典型场景 |
|---|---|---|
| 1 | 单据编号 + 费用名称 | Debit Note 首次入账后再次来单 |
| 2 | 提单号 + 费用名称 + 币种 | 只有 MBL/HBL 的预配/到货通知 |
| 3 | 订舱号 + 费用名称 + 币种 | 船司账单只给 booking no |
| 4 | 箱号 + 交易双方 + 日期 + 金额 | 多渠道到达、单号缺失 |

同一规则命中多行时按**金额距离**选最接近的一条（这正是"一张单里两条同名 Handling Charge"的正确解法），
并把"候选数 / 差额 / 币种冲突"写进审计链。

### 4.5 增量更新：区分「覆盖」与「补全」

题目要求"只修改输入材料明确要求变更的字段"。工程上必须再分一层：

* `overwrite`：台账已有值，被新单据改写 → **才是 UPDATE**；
* `enrich`：台账该字段为空，用新单据补全 → 记账 `NO_CHANGE`，但仍写回台账。

否则要么"每份对账单都被判成 UPDATE"（噪声），要么"空字段永远补不上"（数据烂掉）。

### 4.6 校验与置信度：不达阈值不自动入账

| 校验 | 处理 |
|---|---|
| 金额缺失/非数字 | **error** → PENDING_REVIEW |
| 币种不在 ISO 4217 | **error** → PENDING_REVIEW |
| 单价×数量 ≠ 金额 | warning + 扣分 |
| 明细合计 ≠ 单据总计 | warning + 扣分 |
| OCR `0/O`、`1/I` 混淆 | 数字位自动还原并留痕；标识位只告警不篡改 |
| 箱号 ISO 6346 校验位 | info（提示人工确认） |
| 单据内/跨单据重复费用 | warning；跨单据重复 → PENDING_REVIEW |
| 综合置信度 < 阈值（默认 0.55） | PENDING_REVIEW |

置信度是**可解释的加权和**（明细 0.35 + 类型 0.15 + 方向 0.15 + 编号 0.10 + 双方 0.10 + 匹配 0.15 − 问题惩罚），
不是模型随口给的分。

### 4.7 不虚构：缺失就是 null

* 字段抽不到 → `null`（例如 `PDF 示例账单2.pdf` 的接收方，文本层确实没有，就留空并告警）；
* 只有 `$`/`¥` 符号时按符号映射币种，**不猜**具体银行/账户；
* 附件（B/L）里的业务标识会作为**上下文**补充（箱号、提单号），但不产生费用记录。

---

## 5. OCR：既"真的调 API"，也能离线复现

```bash
# 方式 A：真实调用（OCR 服务需要能下载文件，所以要给公网地址）
python -m shipping_billing run --inputs data/samples \
  --ocr-mode api --ocr-public-base-url https://your-file-host/bills

# 方式 B：离线回放（评审/CI 默认，结果可复现）
python -m shipping_billing run --inputs data/samples --ocr-mode cache

# 方式 C：只认 PDF 文本层（纯文本件，最快）
python -m shipping_billing run --inputs data/samples --ocr-mode text
```

* 调用形态严格按题目给的网关：`POST https://gateway.pltplt.com/ai/process`
  `{"file": url, "task": "orderChange", "callBack": "", "taskType": ""}`，连接 10s / 读取 300s 双超时；
* 返回结构未知 → 递归自适应解析 `text/markdown/content/tables/words`；
* 成功结果按**文件 sha256** 落盘，`cache` 模式可离线回放；失败会明确写 `ocr_source=none` 并告警，**绝不假装识别成功**；
* 连续失败 3 次熔断，避免整批任务被一个坏依赖拖死。

> 说明：`data/ocr_cache/f3ca...json` 是按网关返回结构保存的**离线回放样本**（正文由 `图片示例.png` 转录而来），
> 仅用于无公网环境下的演示与回归；配置 `--ocr-public-base-url` 后即走真实 API。文件头有 `_meta.fixture=true` 标注。

真实 API 连通性可用 `python scripts/ocr_smoke_test.py <公网PDF或图片URL>` 单独验证。

---

## 6. 常用命令

```bash
# 指定输入与记账主体
python -m shipping_billing run --inputs data/samples --entity "CIMC WETRANS INTL LOG CO LTD"

# 换一个主体视角（ZIM 发票 → AP）
python -m shipping_billing run --inputs data/samples/图片示例.png \
  --entity "ALL AMERICA FORWARDING, INC." --out-dir output/ap

# 启用 LLM 增强（任意 OpenAI 兼容端点，默认 DeepSeek）
export LLM_ENABLED=true LLM_API_KEY=sk-xxx LLM_MODEL=deepseek-chat
python -m shipping_billing run --inputs data/samples --llm

# 把更新写回台账
python -m shipping_billing run --inputs data/samples --write-ledger

# 测试
python -m pytest tests -q
```

### 主要参数

| 参数 | 说明 |
|---|---|
| `--inputs` | 文件或目录，可多个；目录递归 |
| `--ledger` | 已有账单台账 JSON |
| `--entity` | **记账主体**（决定 AR/AP 视角），可用 `--entity-alias` 追加别名 |
| `--ocr-mode` | `auto`(默认) / `api` / `cache` / `text` |
| `--ocr-public-base-url` | OCR 服务可访问的文件 URL 前缀（api 模式必需） |
| `--llm` / `--no-llm` | 是否启用 LLM 增强（默认关闭 = 纯规则确定性） |
| `--review-threshold` | 转人工的置信度阈值 |
| `--write-ledger` | 把结果写回台账文件 |

---

## 7. 验收标准对照

详见 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)，逐条对应到代码与测试。

| # | 验收项 | 实现位置 | 证据 |
|---|---|---|---|
| 1 | 解析 EML 正文及附件 | `ingest/eml_loader.py` | 样例 EML 解出 2 个 PDF 附件并递归解析 |
| 2 | 调用 OCR API 识别 PDF/图片 | `ingest/ocr_client.py` | 网关协议 / 重试 / 熔断 / 缓存；`--ocr-mode api` |
| 3 | 解析 Excel、Word 费用表格 | `ingest/excel_loader.py`、`word_loader.py`、`extract/tables.py` | `tests/test_ingest.py` |
| 4 | 准确提取编号/费用/金额/币种 | `extract/rules.py` | 7/7 记录字段准确率 1.0 |
| 5 | 区分新增与更新 | `pipeline_utils.py` | UPDATE/NO_CHANGE/CREATE 各就各位 |
| 6 | 结合记账主体判断 AR/AP | `finance/posting.py` | 同一批文件换主体 → AP |
| 7 | 检测重复账单与冲突 | `matcher.py`、`pipeline.py`、`validators.py` | 同名多行金额判别、跨单据重复转人工 |
| 8 | harness 提升稳定性与准确率 | `harness/*`、`docs/HARNESS.md` | 缓存/重试/熔断/评测/报告 |
| 9 | 5 点以上思考 | `docs/DESIGN_THINKING.md` | 10 点，业务 + 技术 |
| 10 | Python + LangChain 实现 | `agent.py`、`extract/llm_extractor.py` | `RunnableParallel` 组合规则 ∥ LLM |

---

## 8. 已知边界

* **扫描件质量决定 OCR 上限**：低分辨率（<900px 宽）会告警，但仍可能错；系统用"算术自洽 + 校验位 + 低置信度"兜底，而不是盲目入账。
* **PDF 文本层是"视觉乱序"的**：复杂多栏票据的字段位置会串行，`PDF示例账单1.pdf` 的单据日期因此抽不到（输出 `null` + info 提示），这是"不虚构"的必然代价。
* **`.doc` 老格式**：优先用 LibreOffice 转换；没有则退化为二进制文本提取，表格边界会丢失（已告警）。
* **LLM 通道默认关闭**：没有 API Key 时系统仍然完整可用；开启后也只做"补位 + 交叉校验"，不接管金额判定。
* **台账是示例文件**，真实接入需对接财务系统（接口层已抽象为 `matching/ledger.py`）。

---

## 9. 文档

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 架构与数据流、关键决策与取舍
* [`docs/HARNESS.md`](docs/HARNESS.md) —— Harness 工程化设计（验收第 8 条）
* [`docs/DESIGN_THINKING.md`](docs/DESIGN_THINKING.md) —— 10 点思考（验收第 9 条）
* [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) —— 验收标准逐条对照
