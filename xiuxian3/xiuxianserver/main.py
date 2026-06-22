import uvicorn
from fastapi import FastAPI

from launch import config, lifespan, LOGGING_CONFIG, FastAPIAllowed, FastAPIIncludeRouter


def create_app():
    """创建 FastAPI 应用。

    uvicorn 使用 factory 模式调用这个函数，避免 reload 父进程提前创建 app。
    HTTP 路由也在这里注册，这样 /docs 生成时能看到完整接口。
    """

    app = FastAPI(
        title=config.project.name,
        debug=config.project.debug,
        lifespan=lifespan,
    )

    FastAPIAllowed(app)
    FastAPIIncludeRouter(app)

    return app


if __name__ == "__main__":
    uvicorn.run(
        app="main:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        reload=True,
        log_config=LOGGING_CONFIG,
    )
