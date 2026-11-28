# -*- coding: utf-8 -*-
"""成果签收回执 / 传输确认归属测试。

覆盖：
1. 确认入口找不到报告归属时拒绝，不再按文件名广播到全部汇编；
2. 接收方 + 航次都匹配才可核收，同名文件不会写到别的报告；
3. 已完成传输必须能追溯到与报告到件对应的回执；
4. 核收失败保留回执且传输仍可继续确认；
5. 重复提交同一次确认只保留原签收，不写入两轮汇编；
6. 回执只对所属报告版本生效；
7. 甲、乙同名航迹包：确认甲并核收失败，重送进入补充轮，乙不受影响。
"""
from __future__ import annotations

import pytest

from app.services import service_for
from app.services.results_receipt import receipt_service
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()


def _transfer(**values):
    row = {
        "id": 100,
        "status": "传输中",
        "pending": True,
        "abnormal": False,
        "数据内容": "航迹数据-甲",
        "数据量": "12MB",
        "传输方式": "卫星",
        "目标系统": "岸端成果系统",
        "传输状态": "传输中",
        "操作人": "值班员",
        "传输时间": "2026-11-28",
        "航次编号": "RESU-0001",
        "接收方": "甲岸端",
        "文件名": "航迹包.zip",
        "报告ID": 1,
        "报告版本": 1,
        "备注说明": "",
    }
    row.update(values)
    if not any(int(r.get("id", 0)) == int(row["id"]) for r in store.rows("data_transfer")):
        store.rows("data_transfer").append(row)
    return store.find("data_transfer", int(row["id"]))


def _add_transfer(transfer_id: int, **values):
    return _transfer(id=transfer_id, **values)


def test_confirm_without_report_owner_is_rejected():
    """找不到报告归属时拒绝确认，不能按文件名扩散到全部汇编。"""
    svc = service_for("data_transfer")
    entry = _add_transfer(101, 报告ID=None, 接收方="", 航次编号="")
    result, message = svc.run_action(101, "确认完成")
    assert result is None, f"缺少归属不应确认成功：{message}"
    assert "归属" in message
    assert receipt_service.for_transfer(101) == []
    # 所有汇编都没有被写入
    for report in store.rows("results_compile"):
        assert not report.get("实际到件")
        assert "已接收" not in str(report.get("数据完整度", ""))


def test_receiver_and_cruise_must_both_match():
    svc = service_for("data_transfer")
    # 接收方存在但航次不存在：不允许
    _add_transfer(102, 报告ID=None, 接收方="甲岸端", 航次编号="RESU-9999")
    result, message = svc.run_action(102, "确认完成")
    assert result is None
    assert "同时匹配" in message

    # 航次存在但接收方是另一家：不允许
    _add_transfer(103, 报告ID=None, 接收方="乙岸端", 航次编号="RESU-0001")
    result, message = svc.run_action(103, "确认完成")
    assert result is None
    assert "同时匹配" in message

    # 两者都匹配：允许，且只落到该报告
    _add_transfer(104, 报告ID=None, 接收方="甲岸端", 航次编号="RESU-0001")
    result, message = svc.run_action(104, "确认完成")
    assert result is not None, message
    receipts = receipt_service.for_transfer(104)
    assert len(receipts) == 1
    assert receipts[0]["报告ID"] == 1
    assert store.find("results_compile", 1)["实际到件"][0]["回执编号"] == receipts[0]["回执编号"]
    assert not store.find("results_compile", 2).get("实际到件")


def test_same_filename_does_not_cross_reports():
    """同名航迹包：各回各的报告，数据内容不同时不能串。"""
    svc = service_for("data_transfer")
    _add_transfer(110, 数据内容="航迹数据-甲")
    _add_transfer(111, 报告ID=2, 接收方="乙岸端", 航次编号="RESU-0002", 数据内容="航迹数据-乙")
    assert svc.run_action(110, "确认完成")[0] is not None
    assert svc.run_action(111, "确认完成")[0] is not None

    report_a = store.find("results_compile", 1)
    report_b = store.find("results_compile", 2)
    assert [item["数据内容"] for item in report_a["实际到件"]] == ["航迹数据-甲"]
    assert [item["数据内容"] for item in report_b["实际到件"]] == ["航迹数据-乙"]
    assert receipt_service.for_transfer(110)[0]["报告ID"] == 1
    assert receipt_service.for_transfer(111)[0]["报告ID"] == 2


def test_same_content_is_not_duplicated_by_filename_or_report_scan():
    """相同数据内容只登记一次实际到件，不因为同名再写一遍。"""
    svc = service_for("data_transfer")
    _add_transfer(120, 数据内容="航迹数据-甲")
    assert svc.run_action(120, "确认完成")[0] is not None
    # 另一条传输送同一份内容
    _add_transfer(121, 数据内容="航迹数据-甲", 文件名="航迹包-重送.zip")
    assert svc.run_action(121, "确认完成")[0] is not None
    report = store.find("results_compile", 1)
    contents = [item["数据内容"] for item in report["实际到件"]]
    assert contents.count("航迹数据-甲") == 1, f"相同内容不应重复写到报告：{contents}"
    # 但第二次签收仍有独立回执，凭据可追溯
    assert len(receipt_service.for_transfer(121)) == 1


def test_completed_transfer_has_matching_receipt():
    svc = service_for("data_transfer")
    _add_transfer(130, 数据内容="航迹数据-甲")
    entry, message = svc.run_action(130, "确认完成")
    assert entry is not None and entry["status"] == "已完成", message
    result = svc.get_confirmation_result(130)
    assert result["ok"] is True
    assert result["回执缺失"] is False
    assert result["最新回执"]["状态"] == "已核收"
    assert result["报告归属"]["报告ID"] == 1
    assert result["最新回执"]["报告ID"] == 1


def test_checkin_failure_keeps_continue_state_and_receipt():
    svc = service_for("data_transfer")
    _add_transfer(140, 数据内容="航迹数据-甲", 模拟核收失败=True, 核收失败原因="岸端校验未通过")
    entry, message = svc.run_action(140, "确认完成")
    assert entry is not None, message
    assert entry["status"] == "已失败"
    assert entry["可继续确认"] is True
    assert entry["核收结果"] == "核收失败"
    receipts = receipt_service.for_transfer(140)
    assert len(receipts) == 1
    assert receipts[0]["状态"] == "核收失败"
    assert receipts[0]["写入轮次"] is None
    # 报告没有记录这份到件
    assert not store.find("results_compile", 1).get("实际到件")
    # 仍能继续确认（重复确认命中同一张失败回执）
    again, message = svc.run_action(140, "确认完成")
    assert again is not None
    assert len(receipt_service.for_transfer(140)) == 1, "重复确认不应新增回执"


def test_duplicate_confirm_keeps_single_receipt_and_round():
    svc = service_for("data_transfer")
    _add_transfer(150, 数据内容="航迹数据-甲")
    first, _ = svc.run_action(150, "确认完成")
    assert first is not None
    receipt = receipt_service.for_transfer(150)[0]
    round_no = store.find("results_compile", 1)["当前轮次"]
    arrivals = len(store.find("results_compile", 1)["实际到件"])

    second, message = svc.run_action(150, "确认完成")
    assert second is not None
    assert "保留原签收" in message
    assert len(receipt_service.for_transfer(150)) == 1
    assert store.find("results_compile", 1)["当前轮次"] == round_no
    assert len(store.find("results_compile", 1)["实际到件"]) == arrivals == 1
    assert receipt["回执编号"] in message


def test_receipt_only_applies_to_its_report_version():
    svc = service_for("data_transfer")
    report_svc = service_for("results_compile")
    report = store.find("results_compile", 1)
    report["status"] = "已提交"
    report_svc.run_action(1, "审核通过")
    assert report["报告版本"] == 2
    assert report["当前轮次"] == 1

    # v1 的回执不能核收到 v2
    _add_transfer(160, 数据内容="航迹数据-甲", 报告版本=1)
    entry, message = svc.run_action(160, "确认完成")
    assert entry is None
    assert "版本" in message
    assert receipt_service.for_transfer(160) == []

    # 匹配 v2 的传输可以核收
    _add_transfer(161, 数据内容="航迹数据-甲", 报告版本=2)
    entry, message = svc.run_action(161, "确认完成")
    assert entry is not None, message
    receipt = receipt_service.for_transfer(161)[0]
    assert receipt["报告版本"] == 2
    # 详情只展示本版本回执
    detail = report_svc.get_entry(1)
    assert all(row["报告版本"] == 2 for row in detail["签收回执"])
    assert receipt["回执编号"] in [row["回执编号"] for row in detail["签收回执"]]


def test_submit_carries_claimed_materials():
    svc = service_for("data_transfer")
    report_svc = service_for("results_compile")
    _add_transfer(170, 数据内容="航迹数据-甲")
    assert svc.run_action(170, "确认完成")[0] is not None
    entry, message = report_svc.run_action(1, "提交汇编")
    assert entry is not None, message
    materials = entry["提交资料"]
    assert [item["数据内容"] for item in materials] == ["航迹数据-甲"]
    assert materials[0]["回执编号"] == receipt_service.for_transfer(170)[0]["回执编号"]


def test_supplement_conflict_shows_owned_by_existing_report():
    """汇编员退回补充时岸端到件：相同内容已由现有报告接纳，不再写补充轮。"""
    svc = service_for("data_transfer")
    report_svc = service_for("results_compile")
    report = store.find("results_compile", 1)

    _add_transfer(180, 数据内容="航迹数据-甲")
    assert svc.run_action(180, "确认完成")[0] is not None
    report_svc.run_action(1, "提交汇编")
    report_svc.run_action(1, "退回补充")
    assert report["status"] == "需补充"
    supplement_round = report["当前轮次"]
    assert supplement_round == 2

    # 同一条传输重送同一份内容（新的确认尝试）
    store.find("data_transfer", 180).update(status="已失败", 确认尝试=2)
    entry, message = svc.run_action(180, "确认完成")
    assert entry is not None, message
    latest = receipt_service.for_transfer(180)[-1]
    assert latest["状态"] == "已核收"
    assert latest["核收结果"] == "已由现有报告接纳"
    assert latest["写入轮次"] is None
    assert latest["引用凭据"] == receipt_service.for_transfer(180)[0]["签收凭据"]
    assert "已由现有报告接纳" in latest["备注说明"] or "接纳" in latest["备注说明"]
    # 汇编没有多出一条到件，也没有多开一轮
    assert len(report["实际到件"]) == 1
    assert report["当前轮次"] == supplement_round


def test_supplement_round_claims_new_arrival():
    """补充轮的新到件留给补充轮次领取，提交审核时带出。"""
    svc = service_for("data_transfer")
    report_svc = service_for("results_compile")
    report = store.find("results_compile", 1)

    _add_transfer(190, 数据内容="航迹数据-甲")
    assert svc.run_action(190, "确认完成")[0] is not None
    report_svc.run_action(1, "提交汇编")
    report_svc.run_action(1, "退回补充")

    _add_transfer(191, 数据内容="补充航迹数据", 文件名="航迹包.zip")
    entry, message = svc.run_action(191, "确认完成")
    assert entry is not None, message
    receipt = receipt_service.for_transfer(191)[0]
    assert receipt["状态"] == "待补充领取"
    assert receipt["核收结果"] == "留给补充轮次领取"
    assert receipt["写入轮次"] is None
    assert receipt["轮次"] == 2
    assert receipt["引用凭据"] == receipt_service.for_transfer(190)[0]["签收凭据"]
    assert receipt["引用轮次"] == 1

    assert [row["回执编号"] for row in report_svc.get_entry(1)["待补充领取"]] == [
        receipt["回执编号"]]

    report_svc.run_action(1, "提交汇编")
    materials = {item["数据内容"] for item in report["提交资料"]}
    # 提交时带出本报告已认领的全部资料，含补充轮刚领取的这一份。
    assert materials == {"航迹数据-甲", "补充航迹数据"}
    assert [item["数据内容"] for item in report["提交资料"]][-1] == "补充航迹数据"
    assert receipt_service.for_transfer(191)[0]["状态"] == "已认领"
    assert not report_svc.get_entry(1)["待补充领取"]


def test_scenario_jia_yi_same_track_package():
    """甲、乙两个报告的同名航迹包：确认甲并模拟核收失败，重送进补充轮，乙不变。"""
    svc = service_for("data_transfer")
    report_svc = service_for("results_compile")
    report_a = store.find("results_compile", 1)
    report_b = store.find("results_compile", 2)
    b_snapshot = {
        "status": report_b["status"],
        "version": report_b["报告版本"],
        "round": report_b.get("当前轮次"),
        "arrivals": list(report_b.get("实际到件") or []),
        "完整度": report_b["数据完整度"],
    }

    # 甲、乙各一条传输，文件名相同
    _add_transfer(200, 数据内容="航迹数据-甲", 报告ID=1, 接收方="甲岸端",
                  航次编号="RESU-0001", 文件名="航迹包.zip",
                  模拟核收失败=True, 核收失败原因="岸端解包失败")
    _add_transfer(201, 数据内容="航迹数据-甲", 报告ID=2, 接收方="乙岸端",
                  航次编号="RESU-0002", 文件名="航迹包.zip")

    # 1) 确认甲 -> 核收失败
    entry_a, message = svc.run_action(200, "确认完成")
    assert entry_a is not None, message
    assert entry_a["status"] == "已失败"
    assert entry_a["可继续确认"] is True
    assert entry_a["核收结果"] == "核收失败"
    result_a = svc.get_confirmation_result(200)
    assert result_a["ok"] is False
    assert result_a["报告归属"]["报告ID"] == 1
    failed_receipt = result_a["最新回执"]
    assert failed_receipt["状态"] == "核收失败"
    assert failed_receipt["报告ID"] == 1
    assert failed_receipt["写入轮次"] is None
    assert report_a.get("实际到件") in (None, [])

    # 乙报告此时未发生任何变化
    assert report_b["status"] == b_snapshot["status"]
    assert report_b["报告版本"] == b_snapshot['version']
    assert report_b.get("当前轮次") == b_snapshot["round"]
    assert (report_b.get("实际到件") or []) == b_snapshot["arrivals"]
    assert report_b["数据完整度"] == b_snapshot["完整度"]
    assert receipt_service.for_transfer(201) == []

    # 2) 甲报告进入补充轮（汇编员退回补充）
    report_svc.run_action(1, "提交汇编")
    report_svc.run_action(1, "退回补充")
    assert report_a["status"] == "需补充"
    assert report_a["当前轮次"] == 2

    # 3) 重送：重试传输后再次确认
    retry, message = svc.run_action(200, "重试传输")
    assert retry is not None, message
    assert retry["status"] == "待传输"
    resent, message = svc.run_action(200, "确认完成")
    assert resent is not None, message
    assert resent["status"] == "已完成"

    # 4) 查传输确认结果
    result = svc.get_confirmation_result(200)
    assert result["ok"] is True
    assert result["报告归属"]["报告ID"] == 1
    assert result["回执数量"] == 2
    failed, latest = result["历史回执"]
    assert failed["回执编号"] == failed_receipt["回执编号"]
    assert failed["状态"] == "核收失败"
    assert latest["状态"] == "待补充领取"
    assert latest["核收结果"] == "留给补充轮次领取"
    assert latest["轮次"] == 2
    assert latest["写入轮次"] is None

    # 5) 旧轮 / 补充轮引用凭据
    assert latest["引用凭据"] == failed["签收凭据"]
    assert latest["引用轮次"] == failed["轮次"] == 1
    assert failed["签收凭据"] != latest["签收凭据"]

    # 6) 乙报告仍然未变化
    result_b = svc.get_confirmation_result(201)
    assert result_b["回执数量"] == 0
    assert report_b["status"] == b_snapshot["status"]
    assert report_b["报告版本"] == b_snapshot['version']
    assert report_b.get("当前轮次") == b_snapshot["round"]
    assert (report_b.get("实际到件") or []) == b_snapshot["arrivals"]
    assert report_b["数据完整度"] == b_snapshot["完整度"]
    assert report_b.get("签收回执") in (None, [])
    assert [row for row in receipt_service.rows() if row["报告ID"] == 2] == []
