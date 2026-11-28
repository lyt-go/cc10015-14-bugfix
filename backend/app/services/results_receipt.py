"""成果签收回执：记录“哪份传输、送到哪个报告、哪一个报告版本/轮次”。

回执是传输确认与成果汇编之间的唯一凭据：
- 一条回执只对它所登记的报告版本生效；
- 同一次传输确认只保留一条回执，重复提交不会写入第二轮汇编；
- 核收失败也保留回执，但状态为“核收失败”，传输仍可继续确认。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


RECEIPT_MODULE = "results_receipt"

# 回执状态
STATUS_PENDING_CHECKIN = "待核收"
STATUS_ACCEPTED = "已核收"
STATUS_PENDING_SUPPLEMENT = "待补充领取"
STATUS_CHECKIN_FAILED = "核收失败"
STATUS_CLAIMED = "已认领"

# 核收处置：值班员从签收结果就能看出文件归属
DISPOSITION_ACCEPTED = "已由现有报告接纳"
DISPOSITION_SUPPLEMENT = "留给补充轮次领取"
DISPOSITION_FAILED = "核收失败"

# 被视为“报告已经实际收到”的回执状态
ACCEPTED_STATUSES = (STATUS_ACCEPTED, STATUS_CLAIMED)


class ResultsReceiptService(BaseModuleService):
    MODULE = RECEIPT_MODULE
    ENTITY = "成果签收回执"
    OPERATION_TERM = "签收回执"
    LIST_FIELDS = [
        "回执编号", "签收凭据", "传输记录ID", "传输回执号", "报告ID", "航次编号",
        "接收方", "报告版本", "轮次", "文件名", "数据内容", "状态", "核收结果",
        "引用凭据", "签收时间", "备注说明",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = [
        STATUS_PENDING_CHECKIN, STATUS_ACCEPTED, STATUS_PENDING_SUPPLEMENT,
        STATUS_CHECKIN_FAILED, STATUS_CLAIMED,
    ]
    ACTION_RULES: dict[str, str] = {}

    def rows(self) -> list[dict[str, Any]]:
        return store.rows(self.MODULE)

    def find(self, receipt_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, receipt_id)

    def for_transfer(self, transfer_id: int) -> list[dict[str, Any]]:
        rows = [
            row for row in self.rows()
            if _as_int(row.get("传输记录ID")) == _as_int(transfer_id)
        ]
        return sorted(rows, key=lambda item: _as_int(item.get("id")))

    def find_for_transfer(
        self,
        transfer_id: int,
        *,
        report_id: int | None = None,
        attempt: int | None = None,
    ) -> dict[str, Any] | None:
        """同一次传输确认只应有一条回执。

        attempt 用来区分“重送后的再次确认”：同一尝试重复提交时返回原回执。
        """
        for row in self.for_transfer(transfer_id):
            if report_id is not None and _as_int(row.get("报告ID")) != _as_int(report_id):
                continue
            if attempt is not None and _as_int(row.get("确认尝试"), 1) != _as_int(attempt, 1):
                continue
            return row
        return None

    def for_report(
        self,
        report_id: int,
        *,
        version: int | None = None,
        round_no: int | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.rows():
            if _as_int(row.get("报告ID")) != _as_int(report_id):
                continue
            if version is not None and _as_int(row.get("报告版本")) != version:
                continue
            if round_no is not None and _as_int(row.get("轮次")) != round_no:
                continue
            if statuses is not None and row.get("状态") not in statuses:
                continue
            result.append(row)
        return sorted(result, key=lambda item: _as_int(item.get("id")))

    def accepted_for_report(
        self,
        report_id: int,
        *,
        version: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.for_report(
            report_id, version=version, statuses=ACCEPTED_STATUSES)

    def find_prior_accepted(
        self,
        report_id: int,
        data_content: str,
        *,
        version: int | None = None,
        exclude_transfer_id: int | None = None,
    ) -> dict[str, Any] | None:
        """查找同一报告、同一版本已接纳过的相同数据内容。

        只按数据内容去重，绝不按文件名判断是否已经收到。
        """
        if not str(data_content or "").strip():
            return None
        for row in self.accepted_for_report(report_id, version=version):
            if exclude_transfer_id is not None \
                    and _as_int(row.get("传输记录ID")) == _as_int(exclude_transfer_id):
                continue
            if _same(row.get("数据内容"), data_content):
                return row
        return None

    def create_receipt(
        self,
        *,
        transfer: dict[str, Any],
        report: dict[str, Any],
        status: str,
        disposition: str,
        version: int,
        round_no: int,
        attempt: int = 1,
        reference_credential: str = "",
        reference_round: int | None = None,
        written_round: int | None = None,
        reason: str = "",
        report_status: str = "",
    ) -> dict[str, Any]:
        """登记一条回执。同一个传输 + 同一次确认只保留一条。"""
        existing = self.find_for_transfer(
            _as_int(transfer["id"]),
            report_id=_as_int(report["id"]),
            attempt=attempt,
        )
        if existing is not None:
            existing["确认次数"] = _as_int(existing.get("确认次数"), 1) + 1
            return existing

        rows = self.rows()
        receipt_id = max((_as_int(row.get("id")) for row in rows), default=0) + 1
        receipt_no = f"RCPT-{receipt_id:04d}"
        credential = (
            f"SIGN-T{_as_int(transfer['id'])}-R{_as_int(report['id'])}"
            f"-V{version}-L{round_no}-A{attempt}"
        )
        receipt = {
            "id": receipt_id,
            "回执编号": receipt_no,
            "签收凭据": credential,
            "传输记录ID": _as_int(transfer["id"]),
            "传输回执号": str(transfer.get("传输回执号") or ""),
            "报告ID": _as_int(report["id"]),
            "报告编号": str(report.get("报告编号") or ""),
            "航次编号": str(report.get("航次编号") or transfer.get("航次编号") or ""),
            "接收方": str(report.get("接收方") or transfer.get("接收方") or ""),
            "报告版本": version,
            "轮次": round_no,
            "写入轮次": written_round,
            "文件名": str(transfer.get("文件名") or ""),
            "数据内容": str(transfer.get("数据内容") or ""),
            "数据量": str(transfer.get("数据量") or ""),
            "状态": status,
            "核收结果": disposition,
            "引用凭据": reference_credential,
            "引用轮次": reference_round,
            "报告状态快照": report_status,
            "确认尝试": attempt,
            "确认次数": 1,
            "签收时间": _now(),
            "备注说明": reason,
            # 通用资源接口需要的状态字段
            "status": status,
            "pending": status in (STATUS_PENDING_CHECKIN, STATUS_PENDING_SUPPLEMENT),
            "abnormal": status == STATUS_CHECKIN_FAILED,
        }
        rows.append(receipt)
        return receipt

    def update_receipt(self, receipt: dict[str, Any], **updates: Any) -> dict[str, Any]:
        receipt.update(updates)
        status = receipt.get("状态", receipt.get("status"))
        receipt["status"] = status
        receipt["pending"] = status in (STATUS_PENDING_CHECKIN, STATUS_PENDING_SUPPLEMENT)
        receipt["abnormal"] = status == STATUS_CHECKIN_FAILED
        return receipt

    def mark_claimed(self, receipt: dict[str, Any], claimed_round: int | None = None) -> dict[str, Any]:
        if receipt.get("状态") in ACCEPTED_STATUSES:
            return receipt
        return self.update_receipt(
            receipt,
            状态=STATUS_CLAIMED,
            status=STATUS_CLAIMED,
            写入轮次=claimed_round if claimed_round is not None else receipt.get("轮次"),
        )


def _same(left: Any, right: Any) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


receipt_service = ResultsReceiptService()
