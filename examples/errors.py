"""演示服务共用的错误响应文档。

FastAPI 默认只把成功响应和 422 写进 OpenAPI。路由上用 errors(400, 404, ...) 声明业务错误码后，
这些错误码才会出现在契约里：oasdiff 能看到错误码的变化，测试插件也能校验错误响应。
"""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


def errors(*codes: int) -> dict:
    return {code: {"model": ErrorResponse} for code in codes}
