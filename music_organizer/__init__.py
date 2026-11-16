"""本地音乐整理小插件。

一个跑在树莓派 / 小型 Linux 主机上的本地音乐整理工具：
扫描音乐目录、检查歌词与封面缺失、在线获取并写回音频标签，
同时提供 Web 管理界面与终端 CLI 两种用法。
"""

from .version import __version__, APP_TITLE, APP_SUBTITLE

__all__ = ["__version__", "APP_TITLE", "APP_SUBTITLE"]
