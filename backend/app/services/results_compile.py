"""成果汇编领域服务：报告详情关联本报告的成果签收回执。

- 报告只使用“报告ID + 报告版本”命中的回执，不会因为文件名相同而串到别的报告；
- 提交审核时带出本报告已在当前版本认领的资料；
- 退回补充时打开补充轮次，岸端新到件留给补充轮领取。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.results_receipt import receipt_service
from app.store import store


class ResultsCompileService(BaseModuleService):
    MODULE = "results_compile"
    ENTITY = "汇编报告"
    OPERATION_TERM = "提交汇编"
    LIST_FIELDS = [
        "航次编号", "成果类别", "主要发现", "参与人员", "数据完整度",
        "汇编人", "完成日期", "报告编号", "接收方", "备注说明",
    ]
    REQUIRED_FIELDS = ["航次编号", "成果类别", "主要发现"]
    STATUS_ORDER = ["汇编中", "已提交", "已审核", "需补充"]
    NEGATIVE_ACTIONS = ["退回补充"]

    # 兼容“提交审核”这一动作叫法。
    ACTION_RULES = {
        "提交汇编": "已提交",
        "提交审核": "已提交",
        "审核通过": "已审核",
        "退回补充": "需补充",
    }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            report_no = str(values.get("报告编号") or "").strip()
            if not report_no:
                report_no = f"RPT-{int(entry['id']):04d}"
            entry["报告编号"] = report_no
            entry["接收方"] = str(values.get("接收方") or "").strip()
            entry["报告版本"] = max(1, _as_int(values.get("报告版本"), 1))
            entry["当前轮次"] = max(1, _as_int(values.get("当前轮次"), 1))
            entry["实际到件"] = []
            if not entry.get("数据完整度"):
                entry["数据完整度"] = ""
        return entry, missing

    # ------------------------------------------------------------------
    # 详情：关联本报告的签收回执
    # ------------------------------------------------------------------
    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        result = dict(entry)
        result["签收回执"] = self.receipts_for(entry)
        result["实际到件"] = [dict(item) for item in (entry.get("实际到件") or [])]
        result["已认领资料"] = [dict(item) for item in self.claimed_materials(entry)]
        result["待补充领取"] = [
            dict(row) for row in self.pending_supplement_receipts(entry)
        ]
        return result

    def receipts_for(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """只取本报告、本版本的回执；同名文件不会跨报告混入。"""
        version = _as_int(entry.get("报告版本"), 1)
        return [
            dict(row)
            for row in receipt_service.for_report(
                int(entry["id"]), version=version)
        ]

    def claimed_materials(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """本报告当前版本已被接纳/认领的资料。"""
        version = _as_int(entry.get("报告版本"), 1)
        claimed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in receipt_service.accepted_for_report(int(entry["id"]), version=version):
            key = str(row.get("数据内容") or "").strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            claimed.append({
                "数据内容": row.get("数据内容"),
                "文件名": row.get("文件名"),
                "回执编号": row.get("回执编号"),
                "签收凭据": row.get("签收凭据"),
                "轮次": row.get("轮次"),
                "报告版本": row.get("报告版本"),
            })
        return claimed

    def pending_supplement_receipts(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in receipt_service.for_report(
                int(entry["id"]), statuses=("待补充领取",))
        ]

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        idempotent_key = f"{self.MODULE}:{entry_id}:{action}:{_flow_key(entry)}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行，请勿重复操作"

        if action == "审核通过" and entry.get("status") != "已提交":
            return None, "只能审核已提交的汇编报告"
        if action == "退回补充" and entry.get("status") not in ("已提交", "已审核"):
            return None, "只能退回已提交或已审核的汇编报告"

        previous_status = str(entry.get("status") or "")
        entry["status"] = target
        entry["pending"] = target in ("汇编中", "需补充")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        if action == "提交汇编":
            # 补充轮提交时，先把留给本轮领取的到件认领进本报告。
            if previous_status == "需补充" or entry.get("补充轮次"):
                self._claim_pending_supplement(entry)
            # 提交审核时带出本报告当前版本已经认领的资料，仅作快照，不新增到件。
            entry["提交资料"] = self.claimed_materials(entry)
            entry["提交版本"] = _as_int(entry.get("报告版本"), 1)
        if action == "审核通过":
            entry["报告版本"] = _as_int(entry.get("报告版本"), 1) + 1
            entry["当前轮次"] = 1
            entry["提交资料"] = []
        if action == "退回补充":
            supplement = max(
                _as_int(entry.get("当前轮次"), 1) + 1,
                _as_int(entry.get("补充轮次"), 0),
            )
            entry["补充轮次"] = supplement
            entry["当前轮次"] = supplement
            # 已提交但未被接纳的资料不跨轮沿用；回执本身保留，凭据可追溯。
            entry["提交资料"] = []

        store.mark_idempotent(idempotent_key)
        return entry, f"{self.ENTITY}已{action}"

    def _claim_pending_supplement(self, entry: dict[str, Any]) -> None:
        """把留给本补充轮领取的到件认领进报告，只写一次。"""
        round_no = _as_int(entry.get("当前轮次"), 1)
        version = _as_int(entry.get("报告版本"), 1)
        for receipt in receipt_service.for_report(
            int(entry["id"]), version=version, statuses=("待补充领取",)
        ):
            if _as_int(receipt.get("轮次")) > round_no:
                continue
            receipt_service.mark_claimed(receipt, claimed_round=round_no)
            self._record_claimed_material(entry, receipt)

    def _record_claimed_material(self, entry: dict[str, Any], receipt: dict[str, Any]) -> None:
        arrivals = entry.setdefault("实际到件", [])
        data_content = str(receipt.get("数据内容") or "")
        for item in arrivals:
            if str(item.get("数据内容") or "").strip().casefold() == data_content.strip().casefold():
                item.setdefault("回执编号", receipt.get("回执编号"))
                return
        arrivals.append({
            "数据内容": data_content,
            "文件名": receipt.get("文件名", ""),
            "回执编号": receipt.get("回执编号", ""),
            "签收凭据": receipt.get("签收凭据", ""),
            "轮次": receipt.get("写入轮次") or receipt.get("轮次"),
            "报告版本": receipt.get("报告版本"),
            "到件时间": receipt.get("签收时间", ""),
        })
        current = str(entry.get("数据完整度") or "")
        marker = f"已接收：{data_content}"
        if marker not in current:
            entry["数据完整度"] = f"{current} | {marker}（回执 {receipt.get('回执编号')}）"
        entry["transfer_complete"] = True
        entry["pending"] = True

    # ------------------------------------------------------------------
    # 由传输模块调用的兼容入口
    # ------------------------------------------------------------------
    def update_completeness(self, entry_id: int, data_source: str, data_desc: str) -> tuple[dict[str, Any] | None, str]:
        """旧接口保留：不再按内容向所有报告广播，只登记调用方明确指定的报告。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        current = entry.get("数据完整度", "")
        entry["数据完整度"] = f"{current} | [{data_source}] {data_desc}"
        entry["pending"] = True
        return entry, "数据完整度已更新"

    def get_completeness_summary(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        total = len(rows)
        complete = sum(1 for r in rows if r.get("transfer_complete"))
        return {
            "total_reports": total,
            "complete_transfers": complete,
            "completeness_pct": round(complete / total * 100, 1) if total > 0 else 0,
        }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _flow_key(entry: dict[str, Any]) -> str:
    return f"v{_as_int(entry.get('报告版本'), 1)}-r{_as_int(entry.get('当前轮次'), 1)}"
