#!/usr/bin/env bash
# 卸载脚本：默认保留音乐与数据目录，只移除程序与 systemd 服务。
set -euo pipefail

APP_DIR="${MORG_INSTALL_DIR:-/opt/music-organizer}"
PURGE="${1:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行卸载脚本（若安装在用户目录下可加 MORG_INSTALL_DIR）" >&2
  exit 1
fi

for unit in music-organizer.service music-organizer-auto.service; do
  if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit" 2>/dev/null || true
    rm -f "/etc/systemd/system/$unit"
  fi
done
systemctl daemon-reload

if [[ -L /usr/local/bin/morg ]]; then
  rm -f /usr/local/bin/morg
fi

if [[ "$PURGE" == "--purge" ]]; then
  read -r -p "确定删除程序目录 $APP_DIR 吗？音乐文件与该目录无关，不会删除。[y/N] " answer
  if [[ "${answer,,}" == "y" ]]; then
    rm -rf "$APP_DIR"
    echo "已删除 $APP_DIR"
  fi
else
  echo "已移除 systemd 服务与命令入口，程序目录保留：$APP_DIR"
  echo "如需彻底删除：sudo ./uninstall.sh --purge"
fi
