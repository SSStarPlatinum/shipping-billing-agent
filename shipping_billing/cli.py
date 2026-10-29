"""命令行入口。

    python -m shipping_billing run  --inputs data/samples --out-dir output
    python -m shipping_billing demo
    python -m shipping_billing eval
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .harness.evaluation import evaluate, load_expectations
from .harness.report import build_markdown, write_markdown
from .io_utils import ensure_dir, get_logger, write_json
from .pipeline import BillingAgent

logger = get_logger("cli")

SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
LEDGER_PATH = PROJECT_ROOT / "data" / "ledger" / "existing_bills.json"
GOLDEN_PATH = PROJECT_ROOT / "data" / "expectations" / "golden.json"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inputs", nargs="+", help="输入文件或目录（可多个）")
    parser.add_argument("--ledger", help="已有账单台账 JSON 路径")
    parser.add_argument("--entity", help="当前记账主体（决定 AR/AP 视角）")
    parser.add_argument("--entity-alias", action="append", default=[], help="记账主体别名（可重复）")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 output/")
    parser.add_argument(
        "--ocr-mode",
        choices=["auto", "api", "cache", "text"],
        default=None,
        help="OCR 策略：auto/api/cache/text",
    )
    parser.add_argument("--ocr-url", default=None, help="OCR 网关地址")
    parser.add_argument(
        "--ocr-public-base-url",
        default=None,
        help="OCR 服务可访问的文件地址前缀（api 模式必需）",
    )
    parser.add_argument("--ocr-url-map", default=None, help="文件名到公网 URL 的映射 JSON")
    parser.add_argument("--llm", dest="llm", action="store_true", help="启用 LLM 增强")
    parser.add_argument("--no-llm", dest="llm", action="store_false", help="关闭 LLM 增强（默认）")
    parser.set_defaults(llm=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--review-threshold", type=float, default=None, help="转人工的置信度阈值")
    parser.add_argument("--write-ledger", action="store_true", help="把更新结果写回台账文件")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict = {}
    if args.entity:
        overrides["accounting_entity"] = args.entity
    if args.entity_alias:
        overrides["entity_aliases"] = tuple(args.entity_alias)
    if args.ledger:
        overrides["ledger_path"] = Path(args.ledger)
    if args.out_dir:
        overrides["output_dir"] = Path(args.out_dir)
    if args.ocr_mode:
        overrides["ocr_mode"] = args.ocr_mode
    if args.ocr_url:
        overrides["ocr_url"] = args.ocr_url
    if args.ocr_public_base_url:
        overrides["ocr_public_base_url"] = args.ocr_public_base_url
    if args.ocr_url_map:
        overrides["ocr_url_map_path"] = Path(args.ocr_url_map)
    if args.llm is not None:
        overrides["llm_enabled"] = bool(args.llm)
    if args.llm_model:
        overrides["llm_model"] = args.llm_model
    if args.llm_base_url:
        overrides["llm_base_url"] = args.llm_base_url
    if args.llm_api_key:
        overrides["llm_api_key"] = args.llm_api_key
    if args.review_threshold is not None:
        overrides["review_confidence_threshold"] = args.review_threshold
    if args.verbose:
        overrides["verbose"] = True
    return Settings.from_env(**overrides)


def _print_summary(result) -> None:
    summary = result.summary
    print("")
    print("=" * 68)
    print("  处理结果摘要")
    print("=" * 68)
    print(f"  文件: {summary['files']}  单据: {summary['documents']}  记录: {summary['records']}"
          f"  耗时: {summary['duration_s']}s")
    ops = summary.get("operations", {})
    print("  操作: " + "  ".join(f"{k}={v}" for k, v in sorted(ops.items())))
    post = summary.get("posting", {})
    print(f"  方向: AR={post.get('AR', 0)}  AP={post.get('AP', 0)}  待定={post.get('unknown', 0)}")
    issues = summary.get("issues", {})
    print(f"  校验: error={issues.get('error', 0)} warning={issues.get('warning', 0)} info={issues.get('info', 0)}")
    print("-" * 68)
    for record in result.records:
        charge = record.charge_details
        amount = charge.amount if charge.amount is not None else 0
        print(
            f"  [{record.operation:<14}] {str(record.document_info.reference_no or '-'):<18}"
            f" {str(charge.charge_item)[:30]:<32} {amount:>12,.2f} {charge.currency or '':<4}"
            f" {record.financial_attributes.posting_type or '-':<3} conf={record.audit_trail.confidence}"
        )
    if result.failures:
        print("-" * 68)
        for failure in result.failures:
            print(f"  ! {failure['file']} [{failure['stage']}] {failure['error']}")
    print("=" * 68)


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    if not args.inputs:
        print("请用 --inputs 指定输入文件或目录", file=sys.stderr)
        return 2
    agent = BillingAgent(settings)
    result = agent.run(args.inputs)
    paths = agent.write_outputs(result)
    if args.write_ledger and settings.ledger_path:
        agent.ledger.save(settings.ledger_path)
        print(f"已写回台账: {settings.ledger_path}")
    if not args.quiet:
        _print_summary(result)
        print("  输出:")
        for key, path in paths.items():
            print(f"    {key:<9} {path}")
    return 0 if not result.failures else 1


def cmd_demo(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "output" / "demo"
    ensure_dir(out_dir)
    ledger_copy = out_dir / "ledger_input.json"
    if LEDGER_PATH.exists():
        shutil.copyfile(LEDGER_PATH, ledger_copy)
    args.inputs = args.inputs or [str(SAMPLES_DIR)]
    args.ledger = str(ledger_copy)
    args.entity = args.entity or "NEPTUNE SHIPPING LIMITED"
    print(f"演示：输入 {args.inputs}")
    print(f"      台账 {ledger_copy}")
    print(f"      主体 {args.entity}")
    return cmd_run(args)


def cmd_eval(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    if not settings.ledger_path:
        settings = settings.with_overrides(ledger_path=LEDGER_PATH)
    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "output" / "eval"
    ensure_dir(out_dir)

    inputs = args.inputs or [str(SAMPLES_DIR)]
    agent = BillingAgent(settings)
    result = agent.run(inputs)
    agent.write_outputs(result, out_dir)

    golden = GOLDEN_PATH if GOLDEN_PATH.exists() else out_dir / "golden.json"
    if not golden.exists():
        print(f"未找到金标准文件: {golden}", file=sys.stderr)
        return 2
    cases = load_expectations(golden)
    eval_result = evaluate(result.records, cases)

    print("")
    print("=" * 68)
    print("  评测结果")
    print("=" * 68)
    payload = eval_result.to_dict()
    for key in ("total_expected", "matched", "recall", "precision", "operation_accuracy", "posting_accuracy"):
        print(f"  {key:<20} {payload[key]}")
    print(f"  field_accuracy       {payload['field_accuracy']}")
    for mismatch in eval_result.mismatches[:10]:
        print(f"  ! {mismatch}")
    print("=" * 68)

    write_json(out_dir / "eval_report.json", payload)
    eval_section = (
        "## 7. 评测指标\n\n```json\n"
        + __import__("json").dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    markdown = build_markdown(
        records=result.records,
        summary=result.summary,
        settings=settings,
        run_log=result.run_log.to_dict() if result.run_log else None,
        documents=result.documents,
        deterministic=not agent.llm.enabled,
        extra_markdown=eval_section,
    )
    write_markdown(out_dir / "report.md", markdown)

    failed_fields = [k for k, v in payload["field_accuracy"].items() if v < 1.0]
    if failed_fields or payload["recall"] < 1.0:
        print(f"  未完全通过: {failed_fields or 'recall'}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shipping_billing",
        description="多格式文档解析的航运智能账单助手智能体",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="处理指定输入")
    _add_common(run_parser)
    run_parser.set_defaults(func=cmd_run)

    demo_parser = sub.add_parser("demo", help="用内置样例跑一遍完整演示")
    _add_common(demo_parser)
    demo_parser.set_defaults(func=cmd_demo)

    eval_parser = sub.add_parser("eval", help="跑样例并与金标准对比")
    _add_common(eval_parser)
    eval_parser.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:  # Windows 控制台编码兜底
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    import logging

    level = logging.ERROR if getattr(args, "quiet", False) else (
        logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return args.func(args)


__all__ = ["build_parser", "main"]
