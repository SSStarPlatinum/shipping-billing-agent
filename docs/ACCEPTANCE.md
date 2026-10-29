# 验收标准逐条对照

> 对应题目"八、验收标准"。每条给出：实现位置 → 可验证方式 → 实测证据。

| # | 验收标准 | 实现位置 | 如何验证 | 实测证据 |
|---|---|---|---|---|
| 1 | 正确解析 EML 邮件正文及附件 | `ingest/eml_loader.py`<br>`ingest/dispatcher.py` | `pytest tests/test_extract_rules.py::test_eml_body_and_attachments` | 样例 EML 解出主题（RFC2047 解码）、HTML+纯文本正文、2 个 PDF 附件（MBL/HBL）并递归解析；内嵌签名图被识别为 inline 而非业务附件 |
| 2 | 调用 OCR API 识别 PDF 和图片 | `ingest/ocr_client.py`<br>`ingest/image_loader.py`<br>`ingest/pdf_loader.py` | `--ocr-mode api --ocr-public-base-url ...`<br>`python scripts/ocr_smoke_test.py <url>` | 严格按网关协议 `POST /ai/process`，10s/300s 双超时、3 次退避重试、熔断、内容哈希缓存、返回结构自适应解析；图片样例经 OCR 得到 2 条费用 |
| 3 | 解析 Excel、Word 中的费用表格 | `ingest/excel_loader.py`<br>`ingest/word_loader.py`<br>`extract/tables.py` | `pytest tests/test_ingest.py` | 现场生成 xlsx/docx：Excel 表头块 + 费用表 → 2 条费用（THC 300.00 / Doc Fee 50.00 → 已标准化）；Word 段落 + 表格 → 2 条费用（Storage 75.00 / Trucking 320.00） |
| 4 | 准确提取单据编号、费用、金额和币种 | `extract/rules.py` | `python -m shipping_billing eval` | 7/7 记录命中金标准；`reference_no`、`currency`、`document_date` 字段准确率均为 1.0 |
| 5 | 区分新增账单和更新账单 | `pipeline_utils.py::_decide_operation` | `pytest tests/test_pipeline.py::test_end_to_end_samples` | 同一张 Debit Note 同时产出 `UPDATE`（40.00→65.00）/ `NO_CHANGE`（35.00 一致）/ `CREATE`（滞留费 1,310.40）三种操作 |
| 6 | 结合记账主体判断 AR/AP | `finance/posting.py` | `pytest tests/test_matching.py`（判定矩阵）<br>`pytest tests/test_pipeline.py::test_ap_perspective` | 主体=NEPTUNE → AR；同一张 ZIM 发票把主体换成 ALL AMERICA FORWARDING → AP；主体不明 → `posting_type=null` + PENDING_REVIEW |
| 7 | 检测重复账单和数据冲突 | `matching/matcher.py`<br>`pipeline.py::_duplicate_key`<br>`validation/validators.py` | `pytest tests/test_pipeline.py::test_duplicate_document_detection` | ① 同单同名两条费用按金额距离区分；② 同一文件喂两遍 → 第二条 PENDING_REVIEW「重复」；③ 币种冲突/合计不符 → 告警并降置信度 |
| 8 | harness 工程如何提升稳定性和准确性 | `harness/*`<br>`docs/HARNESS.md` | 代码 + 文档 | 输入隔离、重试退避、熔断、内容哈希缓存、双通道交叉校验、确定性、可观测性、金标准评测（详见文档） |
| 9 | 5 点以上自己的思考 | `docs/DESIGN_THINKING.md` | 文档 | 10 点，覆盖业务（AR/AP 视角、重复计费、税务、账期、对账闭环）与技术（可信度分层、匹配可解释、overwrite/enrich、OCR 质量门、幂等、可观测性） |
| 10 | 基于 Python 和 LangChain 实现 | `agent.py`<br>`extract/llm_extractor.py`<br>`extract/document_extractor.py` | 阅读代码 / `--llm` 运行 | `RunnableParallel(规则 ∥ LLM) → RunnableLambda(融合) → normalize`；`ChatPromptTemplate + ChatOpenAI + PydanticOutputParser`；节点可单测、可替换模型 |

---

## 附：题目"七、处理要求"逐条落地

| 要求 | 落地方式 |
|---|---|
| 1. 不得虚构输入材料中不存在的数据 | 所有字段来源可追溯（`provenance`）；抽不到即 `null` 并告警 |
| 2. 无法提取的字段使用 `null` | `PDF 示例账单2.pdf` 的接收方、`PDF示例账单1.pdf` 的日期均为 `null` |
| 3. 金额必须使用数字类型 | `ChargeDetails.amount: float`，Pydantic 强类型；负数为贷记行 |
| 4. 币种统一三位代码 | `normalize/currency.py`，符号/中文/小写全部归一到 ISO 4217 |
| 5. 每个费用项目单独一条记录 | 一条 `ChargeLine` → 一条 `OutputRecord`（样例 3 条费用 → 3 条记录） |
| 6. 更新时不得覆盖未明确要求修改的字段 | `compute_field_changes` 只输出 `overwrite`；其余字段继承台账原值并在审计链说明 |
| 7. OCR 结果不能未经校验直接入账 | 四道闸门：结构 / 算术 / 语义 / 置信度（见 `docs/HARNESS.md` 第 3 节） |
| 8. 最终结果必须是合法 JSON | `OutputRecord.model_dump(mode="json")` 落盘；测试中 `json.loads` 校验 |

> 题目中"七、处理要求"的序号原文缺 7、9（从 6 直接跳到 8，再到 10），此处按实际条目对齐。

---

## 复现命令

```bash
pip install -r requirements.txt
python -m pytest tests -q            # 89 passed
python -m shipping_billing eval      # 全指标 1.0，退出码 0
python -m shipping_billing demo      # 生成 output/demo/results.json + report.md
```
