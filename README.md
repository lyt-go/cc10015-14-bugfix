# 海洋科考航次管理系统

首席科学家、调查队员和船长共同规划航次站位，记录海洋观测数据，签认样品移交后按标准流程归档。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 航次计划 | `cruise_plan` | 航次计划书 | 航次编号、调查海域、起止日期 |
| 站位布设 | `station_deploy` | 站位记录 | 站位编号、经纬度坐标、水深数据 |
| 设备清单 | `equipment_manifest` | 设备记录 | 设备名称、设备类型、出厂编号 |
| 采样记录 | `sampling_record` | 采样单 | 采样站位、采样深度、样品类型 |
| 水文测量 | `hydro_measure` | 水文剖面 | 测量站位、温度剖面、盐度剖面 |
| 生物拖网 | `bio_trawl` | 拖网记录 | 拖网站位、网具型号、拖网深度 |
| 沉积物采样 | `sediment_sample` | 沉积物样品 | 采样站位、采样深度、柱状长度 |
| 数据质控 | `data_qc` | 质控记录 | 数据来源、质控标准、异常值个数 |
| 队员排班 | `crew_schedule` | 排班表 | 队员姓名、岗位职责、作业时段 |
| 气象观测 | `weather_obs` | 气象记录 | 观测时间、风速风向、浪高数据 |
| 样品登记 | `sample_registry` | 样品登记簿 | 样品编号、样品类型、采集站位 |
| 分析申请 | `analysis_request` | 分析申请单 | 申请样品、分析项目、分析方法 |
| 航次日志 | `cruise_log` | 航海日志 | 记录日期、船舶位置、作业内容 |
| 仪器校准 | `instrument_calib` | 校准记录 | 仪器名称、校准项目、标准参数 |
| 潜水作业 | `diving_ops` | 潜水记录 | 潜水地点、潜水深度、潜水时长 |
| 数据传输 | `data_transfer` | 传输记录 | 数据内容、数据量、传输方式 |
| 航迹记录 | `track_record` | 航迹点 | 记录时间、经纬度、航速 |
| 成果汇编 | `results_compile` | 汇编报告 | 航次编号、成果类别、主要发现 |

## 传输确认与成果签收回执

传输确认不再按“数据内容/文件名”扫描全部汇编，而是先解析报告归属：

- 归属只认 `报告ID`，或 `接收方 + 航次编号` 同时匹配；找不到归属直接拒绝确认。
- 确认完成生成一条**成果签收回执**（`results_receipt`，固定接口
  `GET /api/receipts`），回执绑定报告ID、报告版本与轮次。
- 同一次确认重复提交只保留原回执，不写入第二轮汇编；回执只对所属报告版本生效。
- 核收失败会保留失败回执，传输回到 `已失败` 但 `可继续确认`，重试后可再次确认。
- 汇编报告处于 `需补充` 时，新到件登记为 `待补充领取`，引用旧轮签收凭据，
  在补充轮提交汇编时认领；若同内容已被现有报告接纳，则签收结果显示
  `已由现有报告接纳`，不再重复写入补充轮。

相关接口：

- `GET /api/data_transfer/{id}/confirmation`：报告归属、签收历史、旧轮/补充轮引用凭据。
- `GET /api/results_compile/{id}/receipts`：本报告当前版本的回执、实际到件与已认领资料。
- `GET /api/receipts?report_id=&transfer_id=&status=`：按报告/传输/状态查回执。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：应用开发
子方向：服务端开发
