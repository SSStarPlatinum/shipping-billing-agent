# 示例输出

这里放两份**真实运行产生**的结果，可以直接打开看：

## 1. `demo/` —— 默认视角（NEPTUNE SHIPPING LIMITED，AR）

```bash
python -m shipping_billing eval --out-dir examples/demo
```

输入：`data/samples` 下全部 4 个样例（1 封 EML + 2 个 PDF + 1 张 PNG）

| 文件 | 内容 |
|---|---|
| `results.json` | 7 条标准输出记录（题目要求的 JSON 数组） |
| `report.md` | 运行报告 + 每条记录的处理依据 + 评测指标 |
| `ledger_after.json` | 处理后的台账（含 1 条 UPDATE、1 条 CREATE） |
| `run_log.json` | 逐文件耗时 / OCR 来源 / 错误 |
| `eval_report.json` | 与金标准比对的指标 |

结果分布：`UPDATE ×1`、`NO_CHANGE ×2`、`CREATE ×1`、`PENDING_REVIEW ×3`；`AR ×4`、方向待定 ×3。

三条 `PENDING_REVIEW` 都是**设计使然**，不是抽不出来：

* 2 条来自 ZIM 发票：签发方/接收方都不是 NEPTUNE，按题目"不能仅凭单据类型判断 AR/AP"的要求，不允许猜；
* 1 条来自内部 PRE-ALERT 邮件：交易双方都是邮箱地址，无法与记账主体匹配。

## 2. `zim_ap/` —— 换主体视角（ALL AMERICA FORWARDING, INC.，AP）

```bash
python -m shipping_billing run --inputs data/samples/图片示例.png \
  --ledger data/ledger/existing_bills.json \
  --entity "ALL AMERICA FORWARDING, INC." \
  --out-dir examples/zim_ap
```

同一张 ZIM 发票，主体换成收票方后：

* `posting_type` 从 `null` 变成 **`AP`**；
* 两条费用分别命中匹配规则 3（订舱号）与规则 4（箱号+双方+日期+金额），均为 `NO_CHANGE`。

这组对比说明：**AR/AP 由"谁在记账"决定，而不是由单据类型决定**。
