"""api-agent：写接口业务测试用的 pytest 插件。

提供 api / db / admin fixture，每条测试自动记录请求、响应和数据库查询，
失败时把证据写进报告和 evidence 目录。
"""

__version__ = "0.2.0"
