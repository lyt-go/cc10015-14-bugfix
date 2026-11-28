"""数据传输领域服务：传输确认只对“它自己的报告”生效。

修复要点：
- 确认入口不再按数据内容/文件名扫描全部汇编，而是先解析报告归属
  （报告ID，或接收方 + 航次编号同时匹配），找不到归属就拒绝确认；
- 每次确认生成一条成果签收回执，回执绑定报告版本与轮次；
- 同一次确认重复提交只保留原回执，不会重复写入汇编轮次；
- 核收失败仍把传输留在可继续确认/重送的状态，并保留失败回执。
"""
from __future__ import annotations

from typing import Any

from app.services import results_receipt as receipt_domain
from app.services.base import BaseModuleService
from app.services.results_receipt import receipt_service
from app.store import store


class DataTransferService(BaseModuleService):
    MODULE = "data_transfer"
    ENTITY = "传输记录"
    OPERATION_TERM = "发起传输"
    LIST_FIELDS = [
        "数据内容", "数据量", "传输方式", "目标系统", "传输状态", "操作人",
        "传输时间", "航次编号", "接收方", "文件名", "报告ID", "报告版本", "备注说明",
    ]
    REQUIRED_FIELDS = ["数据内容", "数据量", "传输方式", "接收方", "航次编号"]
    STATUS_ORDER = ["待传输", "传输中", "已完成", "已失败"]
    ACTION_RULES = {
        "发起传输": "传输中",
        "确认完成": "已完成",
        "重试传输": "待传输",
        "核收失败": "已失败",
    }
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 查询：详情里直接带出这份传输的签收回执和报告归属
    # ------------------------------------------------------------------
    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        result = dict(entry)
        result["报告归属"] = self._report_brief(entry)
        receipts = [dict(row) for row in receipt_service.for_transfer(entry_id)]
        result["签收历史"] = receipts
        result["签收回执"] = receipts[-1] if receipts else None
        result["传输确认结果"] = self.get_confirmation_result(entry_id)
        return result

    def get_confirmation_result(self, entry_id: int) -> dict[str, Any]:
        """返回传输确认的可读结果：是否完成、归属报告、回执与轮次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}
        receipts = [dict(row) for row in receipt_service.for_transfer(entry_id)]
        latest = receipts[-1] if receipts else None
        accepted = any(
            row.get("状态") in receipt_domain.ACCEPTED_STATUSES for row in receipts)
        completed = entry.get("status") == "已完成"
        return {
            "ok": completed,
            "传输记录ID": int(entry_id),
            "传输状态": entry.get("status"),
            "核收结果": entry.get("核收结果", ""),
            "可继续确认": bool(entry.get("可继续确认")),
            # 已完成必须有与报告到件对应的回执，否则标记缺凭据。
            "回执缺失": completed and not accepted,
            "报告归属": self._report_brief(entry),
            "最新回执": latest,
            "回执数量": len(receipts),
            "历史回执": receipts,
        }

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

        if action == "确认完成":
            return self._confirm(entry)
        if action == "核收失败":
            return self._fail_checkin(entry)

        attempt = self._attempt(entry)
        idempotent_key = f"{self.MODULE}:{entry_id}:{action}:{attempt}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行，请勿重复操作"

        if action == "重试传输" and entry.get("status") != "已失败":
            return None, "只能重试已失败的传输"

        tx_id = f"{self.MODULE}:{entry_id}:{action}:{attempt}:tx"
        store.save_snapshot(tx_id, self.MODULE, entry_id)

        entry["status"] = target
        entry["pending"] = target in ("待传输", "传输中")
        entry["abnormal"] = False
        entry["可继续确认"] = target in ("待传输", "传输中")

        if action == "重试传输":
            entry["retry_count"] = _as_int(entry.get("retry_count")) + 1
            entry["last_retry_time"] = receipt_domain._now()
            # 重送视为一次新的确认尝试；旧回执保留，不会被覆盖。
            entry["确认尝试"] = self._attempt(entry) + 1

        store.mark_idempotent(idempotent_key)
        return entry, f"{self.ENTITY}已{action}"

    def _confirm(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        attempt = self._attempt(entry)

        # 同一次确认重复提交：直接返回原签收，不再写入新的汇编轮次。
        existing = receipt_service.find_for_transfer(int(entry["id"]), attempt=attempt)
        if existing is None and entry.get("status") == "已完成":
            # 已完成后再点确认，仍然是同一次签收，不产生第二轮。
            history = receipt_service.for_transfer(int(entry["id"]))
            existing = history[-1] if history else None
        if existing is not None:
            self._apply_receipt_to_transfer(entry, existing)
            if existing.get("状态") == receipt_domain.STATUS_CHECKIN_FAILED:
                entry["status"] = "已失败"
                entry["pending"] = True
                entry["可继续确认"] = True
                return entry, (
                    f"该次确认已登记核收失败，保留原签收 {existing.get('回执编号')}；"
                    f"可重试传输后继续确认"
                )
            entry["status"] = "已完成"
            entry["pending"] = existing.get("状态") == receipt_domain.STATUS_PENDING_SUPPLEMENT
            entry["abnormal"] = False
            entry["可继续确认"] = False
            return entry, (
                f"该次确认已签收，保留原签收 {existing.get('回执编号')}"
                f"（报告 {existing.get('报告ID')}，版本 {existing.get('报告版本')}，"
                f"轮次 {existing.get('轮次')}）"
            )

        report, error = self._resolve_report(entry)
        if report is None:
            return None, error

        version = self._transfer_version(entry, report)
        report_version = self._report_version(report)
        if version != report_version:
            return None, (
                f"传输回执只对报告版本 {version} 生效，"
                f"报告「{report.get('报告编号') or report.get('id')}」当前版本为 {report_version}"
            )

        report_status = str(report.get("status") or "")
        # 已审核的报告只接受“当前（新）版本”的到件：
        # 旧版本回执由下面的版本校验挡掉，新版本到件照常登记。

        data_content = str(entry.get("数据内容") or "").strip()
        round_no = self._current_round(report)
        status = receipt_domain.STATUS_ACCEPTED
        disposition = receipt_domain.DISPOSITION_ACCEPTED
        write_round: int | None = round_no
        reference_credential = ""
        reference_round: int | None = None
        reason = ""

        prior_accepted = receipt_service.find_prior_accepted(
            int(report["id"]),
            data_content,
            version=report_version,
        )

        if report_status == "需补充":
            round_no = self._supplement_round(report)
            if prior_accepted is not None:
                # 冲突：岸端到件时报告已被退回补充，但同内容已被现有报告接纳。
                disposition = receipt_domain.DISPOSITION_ACCEPTED
                write_round = None
                # 归入原接纳轮次，引用原签收凭据，补充轮不再重复登记。
                round_no = _as_int(prior_accepted.get("轮次"), round_no)
                reference_credential = str(prior_accepted.get("签收凭据") or "")
                reference_round = round_no
                reason = (
                    f"汇编员已退回补充，但相同数据内容已由回执 "
                    f"{prior_accepted.get('回执编号')} 接纳，不重复写入"
                )
            else:
                status = receipt_domain.STATUS_PENDING_SUPPLEMENT
                disposition = receipt_domain.DISPOSITION_SUPPLEMENT
                write_round = None
                prior = self._prior_round_receipt(
                    report, report_version, round_no)
                if prior is not None:
                    reference_credential = str(prior.get("签收凭据") or "")
                    reference_round = _as_int(prior.get("轮次")) or None
                reason = f"报告处于补充状态，文件留给第 {round_no} 轮领取"

        if _truthy(entry.get("模拟核收失败")):
            status = receipt_domain.STATUS_CHECKIN_FAILED
            disposition = receipt_domain.DISPOSITION_FAILED
            write_round = None
            reason = str(entry.get("核收失败原因") or "岸端核收失败")
            if reference_credential == "":
                prior = self._prior_round_receipt(
                    report, report_version, round_no)
                if prior is not None:
                    reference_credential = str(prior.get("签收凭据") or "")
                    reference_round = _as_int(prior.get("轮次")) or None

        receipt = receipt_service.create_receipt(
            transfer=entry,
            report=report,
            status=status,
            disposition=disposition,
            version=version,
            round_no=round_no,
            attempt=attempt,
            reference_credential=reference_credential,
            reference_round=reference_round,
            written_round=write_round,
            reason=reason,
            report_status=report_status,
        )
        entry.pop("模拟核收失败", None)
        entry.pop("核收失败原因", None)
        self._apply_receipt_to_transfer(entry, receipt)

        if status == receipt_domain.STATUS_CHECKIN_FAILED:
            # 核收失败：不写汇编，但传输保留“可继续确认”的状态。
            entry["status"] = "已失败"
            entry["pending"] = True
            entry["abnormal"] = False
            entry["可继续确认"] = True
            entry["核收结果"] = disposition
            entry["核收失败原因"] = reason
            # 保留本次尝试号：重复确认仍落到这张失败回执，重送才进入下一次尝试。
            entry["确认尝试"] = attempt
            return entry, (
                f"{self.ENTITY}核收失败（{receipt.get('回执编号')}），"
                f"报告未写入到件；可重试传输后继续确认"
            )

        if disposition == receipt_domain.DISPOSITION_ACCEPTED and prior_accepted is None:
            # 只登记实际到件；相同数据内容不因文件名相同而重复写。
            self._record_actual_arrival(report, receipt)

        entry["status"] = "已完成"
        entry["pending"] = status == receipt_domain.STATUS_PENDING_SUPPLEMENT
        entry["abnormal"] = False
        entry["可继续确认"] = False
        entry["核收结果"] = disposition
        entry["核收轮次"] = receipt.get("轮次")
        entry["引用凭据"] = receipt.get("引用凭据")
        entry["引用轮次"] = receipt.get("引用轮次")

        return entry, (
            f"{self.ENTITY}已确认完成，签收回执 {receipt.get('回执编号')}；"
            f"报告 {report.get('报告编号') or report.get('id')} "
            f"版本 {version} 第 {round_no} 轮：{disposition}"
        )

    def _fail_checkin(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """把最近一次签收标为核收失败（供岸端回传核收结果）。"""
        receipts = receipt_service.for_transfer(int(entry["id"]))
        if not receipts:
            return None, "该传输尚无签收回执，无法登记核收失败"
        receipt = receipts[-1]
        if receipt.get("状态") == receipt_domain.STATUS_CHECKIN_FAILED:
            return entry, f"核收失败已登记（{receipt.get('回执编号')}），可继续确认"
        receipt_service.update_receipt(
            receipt,
            状态=receipt_domain.STATUS_CHECKIN_FAILED,
            status=receipt_domain.STATUS_CHECKIN_FAILED,
            核收结果=receipt_domain.DISPOSITION_FAILED,
            写入轮次=None,
            备注说明=str(entry.get("核收失败原因") or "岸端核收失败"),
        )
        report = store.find("results_compile", int(receipt.get("报告ID", 0)))
        if report is not None:
            self._remove_actual_arrival(report, receipt)
        entry["status"] = "已失败"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["可继续确认"] = True
        entry["核收结果"] = receipt_domain.DISPOSITION_FAILED
        entry["确认尝试"] = self._attempt(entry) + 1
        entry["核收失败原因"] = receipt.get("备注说明", "")
        return entry, (
            f"已登记核收失败（{receipt.get('回执编号')}），"
            f"报告未保留该到件；可重试传输后继续确认"
        )

    # ------------------------------------------------------------------
    # 报告归属解析（根因修复点）
    # ------------------------------------------------------------------
    def _resolve_report(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """解析传输对应的汇编报告。

        归属只认报告ID，或“接收方 + 航次编号”同时匹配；
        文件名、数据内容都只是载荷描述，绝不用于判断报告归属。
        """
        reports = store.rows("results_compile")
        raw_report_id = entry.get("报告ID")
        if raw_report_id not in (None, "", 0, "0"):
            try:
                report_id = int(raw_report_id)
            except (TypeError, ValueError):
                return None, f"传输记录声明的报告ID「{raw_report_id}」无效"
            report = store.find("results_compile", report_id)
            if report is None:
                return None, f"传输记录声明的报告 {report_id} 不存在，无法确认到件归属"
            mismatch = self._match_error(entry, report)
            if mismatch:
                return None, mismatch
            return report, ""

        receiver = str(entry.get("接收方") or "").strip()
        cruise = str(entry.get("航次编号") or "").strip()
        if not receiver or not cruise:
            return None, (
                "传输记录缺少报告归属：请补充报告ID，或同时填写接收方和航次编号；"
                "不能按文件名或数据内容猜测报告"
            )
        candidates = [
            row for row in reports
            if str(row.get("接收方") or "").strip() == receiver
            and str(row.get("航次编号") or "").strip() == cruise
        ]
        if not candidates:
            return None, (
                f"找不到接收方「{receiver}」、航次「{cruise}」同时匹配的汇编报告，"
                f"不能按文件名「{entry.get('文件名', '')}」确认"
            )
        if len(candidates) > 1:
            return None, (
                f"接收方「{receiver}」、航次「{cruise}」匹配到多份汇编报告，"
                f"请指定报告ID"
            )
        return candidates[0], ""

    def _match_error(self, entry: dict[str, Any], report: dict[str, Any]) -> str:
        receiver = str(entry.get("接收方") or "").strip()
        cruise = str(entry.get("航次编号") or "").strip()
        if receiver and receiver != str(report.get("接收方") or "").strip():
            return (
                f"接收方不匹配：传输为「{receiver}」，"
                f"报告 {report.get('id')} 为「{report.get('接收方')}」"
            )
        if cruise and cruise != str(report.get("航次编号") or "").strip():
            return (
                f"航次不匹配：传输为「{cruise}」，"
                f"报告 {report.get('id')} 为「{report.get('航次编号')}」"
            )
        return ""

    def _report_brief(self, entry: dict[str, Any]) -> dict[str, Any]:
        report, error = self._resolve_report(entry)
        if report is None:
            return {"报告ID": entry.get("报告ID"), "可定位": False, "说明": error}
        return {
            "报告ID": report.get("id"),
            "报告编号": report.get("报告编号"),
            "航次编号": report.get("航次编号"),
            "接收方": report.get("接收方"),
            "报告版本": self._report_version(report),
            "当前轮次": self._current_round(report),
            "可定位": True,
        }

    # ------------------------------------------------------------------
    # 汇编写入
    # ------------------------------------------------------------------
    def _record_actual_arrival(self, report: dict[str, Any], receipt: dict[str, Any]) -> None:
        """按数据内容登记实际到件；文件名不参与判重。"""
        arrivals = report.setdefault("实际到件", [])
        data_content = str(receipt.get("数据内容") or "")
        for item in arrivals:
            if receipt_domain._same(item.get("数据内容"), data_content):
                # 已接纳过相同内容：不重复写入，但保留新回执关联。
                item.setdefault("回执编号", receipt.get("回执编号"))
                return
        arrivals.append({
            "数据内容": data_content,
            "文件名": receipt.get("文件名", ""),
            "回执编号": receipt.get("回执编号", ""),
            "签收凭据": receipt.get("签收凭据", ""),
            "轮次": receipt.get("轮次"),
            "报告版本": receipt.get("报告版本"),
            "到件时间": receipt.get("签收时间", ""),
        })
        current = str(report.get("数据完整度") or "")
        marker = f"已接收：{data_content}"
        if marker not in current:
            report["数据完整度"] = f"{current} | {marker}（回执 {receipt.get('回执编号')}）"
        report["transfer_complete"] = True
        report["last_transfer_time"] = receipt.get("签收时间", "")
        report["pending"] = True

    def _remove_actual_arrival(self, report: dict[str, Any], receipt: dict[str, Any]) -> None:
        receipt_no = str(receipt.get("回执编号") or "")
        arrivals = report.get("实际到件") or []
        kept = [
            item for item in arrivals
            if str(item.get("回执编号") or "") != receipt_no
        ]
        report["实际到件"] = kept
        if len(kept) != len(arrivals):
            data_content = str(receipt.get("数据内容") or "")
            current = str(report.get("数据完整度") or "")
            fragment = f" | 已接收：{data_content}（回执 {receipt_no}）"
            report["数据完整度"] = current.replace(fragment, "")
        report["transfer_complete"] = bool(kept)

    def _apply_receipt_to_transfer(self, entry: dict[str, Any], receipt: dict[str, Any]) -> None:
        entry["签收回执号"] = receipt.get("回执编号")
        entry["核收结果"] = receipt.get("核收结果")
        entry["报告ID"] = receipt.get("报告ID")
        entry["报告版本"] = receipt.get("报告版本")
        entry["核收轮次"] = receipt.get("轮次")
        entry["引用凭据"] = receipt.get("引用凭据")
        entry["引用轮次"] = receipt.get("引用轮次")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _last_receipt(self, report: dict[str, Any], version: int) -> dict[str, Any] | None:
        rows = receipt_service.for_report(int(report["id"]), version=version)
        return rows[-1] if rows else None

    def _prior_round_receipt(
        self,
        report: dict[str, Any],
        version: int,
        round_no: int,
    ) -> dict[str, Any] | None:
        """补充轮引用的是“上一轮（旧轮）”的签收凭据。

        旧轮凭据按报告版本 + 轮次取，重送前的失败回执也算旧轮凭据。
        """
        rows = [
            row for row in receipt_service.for_report(
                int(report["id"]), version=version)
            if _as_int(row.get("轮次")) < round_no
        ]
        if not rows:
            return None
        accepted = [
            row for row in rows
            if row.get("状态") in receipt_domain.ACCEPTED_STATUSES
        ]
        pool = accepted or rows
        return pool[-1]

    def _attempt(self, entry: dict[str, Any]) -> int:
        return max(1, _as_int(entry.get("确认尝试"), 1))

    def _report_version(self, report: dict[str, Any]) -> int:
        return max(1, _as_int(report.get("报告版本"), 1))

    def _transfer_version(self, entry: dict[str, Any], report: dict[str, Any]) -> int:
        raw = entry.get("报告版本")
        if raw in (None, ""):
            return self._report_version(report)
        return _as_int(raw, self._report_version(report))

    def _current_round(self, report: dict[str, Any]) -> int:
        return max(1, _as_int(report.get("当前轮次"), 1))

    def _supplement_round(self, report: dict[str, Any]) -> int:
        # 退回补充时已经开出了待补充轮次（当前轮次），新到件就留给这一轮领取。
        return self._current_round(report)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in ("1", "true", "yes", "是", "已核收失败")
