"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

固定路径（/api/receipts、/api/data_transfer/{id}/confirmation）必须先于
动态资源路由 /api/{module} 注册，否则会被动态路径抢先匹配。
"""
from __future__ import annotations

from app.routers import receipt, resource

ROUTERS = [receipt, resource]
