"""本地 Web 服务包（标准库实现，无第三方 Web 框架）。"""
from .app import App, create_server, serve

__all__ = ["App", "create_server", "serve"]
