"""成果签收回执查询接口：按报告、传输或状态查看签收凭据。

固定路径 `/api/receipts` 必须独立成一个路由，避免被 `/{module}` 动态路径吞掉。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.results_receipt import RECEIPT_MODULE
from app.store import store

router = APIRouter(prefix="/api", tags=["成果签收回执"])


def _filtered(
    report_id: int | None = None,
    transfer_id: int | None = None,
    status: str | None = None,
    version: int | None = None,
    round_no: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in store.rows(RECEIPT_MODULE):
        if report_id is not None and int(row.get("报告ID", 0)) != report_id:
            continue
        if transfer_id is not None and int(row.get("传输记录ID", 0)) != transfer_id:
            continue
        if status and row.get("状态") != status:
            continue
        if version is not None and int(row.get("报告版本", 0)) != version:
            continue
        if round_no is not None and int(row.get("轮次", 0)) != round_no:
            continue
        rows.append(dict(row))
    return sorted(rows, key=lambda item: int(item.get("id", 0)))


@router.get("/receipts")
def list_receipts(
    report_id: int | None = Query(default=None),
    transfer_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    version: int | None = Query(default=None),
    round_no: int | None = Query(default=None),
) -> dict[str, Any]:
    """按报告/传输/状态查询回执；同名文件不会把不同报告的回执混在一起。"""
    rows = _filtered(report_id, transfer_id, status, version, round_no)
    return {"items": rows, "total": len(rows)}


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: int) -> dict[str, Any]:
    row = store.find(RECEIPT_MODULE, receipt_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"回执 {receipt_id} 不存在")
    return dict(row)


@router.get("/data_transfer/{entry_id}/confirmation")
def transfer_confirmation(entry_id: int) -> dict[str, Any]:
    """传输确认结果：报告归属、回执、旧轮与补充轮引用凭据。"""
    from app.services import service_for

    service = service_for("data_transfer")
    if service is None:
        raise HTTPException(status_code=404, detail="数据传输模块不可用")
    result = service.get_confirmation_result(entry_id)
    if not result.get("ok", True) and result.get("message"):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/results_compile/{entry_id}/receipts")
def compile_receipts(entry_id: int) -> dict[str, Any]:
    """成果汇编详情里的成果签收回执，仅本报告当前版本。"""
    from app.services import service_for

    service = service_for("results_compile")
    if service is None:
        raise HTTPException(status_code=404, detail="成果汇编模块不可用")
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"汇编报告 {entry_id} 不存在")
    return {
        "报告ID": entry_id,
        "报告版本": entry.get("报告版本", 1),
        "当前轮次": entry.get("当前轮次", 1),
        "成果签收回执": entry.get("签收回执", []),
        "实际到件": entry.get("实际到件", []),
        "已认领资料": entry.get("已认领资料", []),
        "待补充领取": entry.get("待补充领取", []),
    }
