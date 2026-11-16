#!/usr/bin/env bash
# =============================================================================
# 拾音 · 本地音乐整理小插件 一键安装脚本
#
# 默认安装到 /opt/music-organizer（需要 sudo），并注册 systemd 服务。
# 常用环境变量：
#   MORG_INSTALL_DIR=/opt/music-organizer   安装目录
#   MORG_USER=wu                            运行服务的系统用户（默认 sudo 用户）
#   MORG_PORT=8090                          Web 端口
#   MORG_NO_SERVICE=1                       只装 CLI，不注册 systemd
#   MORG_PIP_INDEX=https://...              自定义 pip 源
#   PYTHON_BIN=python3                      指定 Python
#
# 用法：
#   sudo ./install.sh
#   sudo MORG_PORT=8091 ./install.sh
#   MORG_INSTALL_DIR="$HOME/music-organizer" MORG_NO_SERVICE=1 ./install.sh
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${MORG_PORT:-8090}"
INSTALL_SERVICE="${MORG_NO_SERVICE:-0}"

if [[ "${EUID}" -eq 0 ]]; then
  DEFAULT_DIR="/opt/music-organizer"
  SERVICE_USER="${MORG_USER:-${SUDO_USER:-root}}"
else
  DEFAULT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/music-organizer/app"
  SERVICE_USER="${MORG_USER:-$(id -un)}"
fi
APP_DIR="${MORG_INSTALL_DIR:-$DEFAULT_DIR}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER" 2>/dev/null || echo "$SERVICE_USER")"

log()  { printf '\033[1;35m[拾音]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

log "安装目录：$APP_DIR"
log "运行用户：$SERVICE_USER（组 $SERVICE_GROUP）"

# ---------------------------------------------------------------------------
# 1. 检查环境
# ---------------------------------------------------------------------------
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 $PYTHON_BIN，请先安装 Python 3.9+"
"$PYTHON_BIN" - <<'PY' || die "需要 Python 3.9 或更新版本"
import sys
if sys.version_info < (3, 9):
    raise SystemExit(1)
print(f"  ✓ Python {sys.version.split()[0]}")
PY

if [[ "${EUID}" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
  missing=()
  "$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || missing+=(python3-venv)
  "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || missing+=(python3-pip)
  if ((${#missing[@]})); then
    log "安装系统依赖：${missing[*]}"
    apt-get update -qq && apt-get install -y -qq "${missing[@]}"
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "未检测到 ffmpeg（可选，仅影响格式转换等扩展功能）"
fi

# ---------------------------------------------------------------------------
# 2. 复制代码
# ---------------------------------------------------------------------------
mkdir -p "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  log "复制程序文件到 $APP_DIR"
  rm -rf "$APP_DIR/music_organizer" "$APP_DIR/tools" "$APP_DIR/tests"
  cp -a "$SRC_DIR/music_organizer" "$APP_DIR/"
  cp -a "$SRC_DIR/requirements.txt" "$APP_DIR/"
  [[ -f "$SRC_DIR/README.md" ]] && cp -a "$SRC_DIR/README.md" "$APP_DIR/"
  [[ -f "$SRC_DIR/LICENSE" ]] && cp -a "$SRC_DIR/LICENSE" "$APP_DIR/"
fi
mkdir -p "$APP_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$APP_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 3. 创建虚拟环境并安装依赖
# ---------------------------------------------------------------------------
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  log "创建 Python 虚拟环境（复用系统已安装的包，减少下载）"
  "$PYTHON_BIN" -m venv --system-site-packages "$APP_DIR/venv"
fi
VENV_PY="$APP_DIR/venv/bin/python"
PIP_ARGS=(--disable-pip-version-check --no-cache-dir)
if [[ -n "${MORG_PIP_INDEX:-}" ]]; then
  PIP_ARGS+=(--index-url "$MORG_PIP_INDEX")
fi
log "安装 Python 依赖（Flask / mutagen / Pillow / waitress / zhconv）"
"$VENV_PY" -m pip install "${PIP_ARGS[@]}" --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV_PY" -m pip install "${PIP_ARGS[@]}" -r "$APP_DIR/requirements.txt"; then
  if "$VENV_PY" -c "import flask, mutagen, PIL" >/dev/null 2>&1; then
    warn "在线安装依赖失败，但系统已有 Flask / mutagen / Pillow，继续安装（可能缺少 waitress / zhconv）"
    warn "如需补齐，可设置 MORG_PIP_INDEX 后重跑：sudo MORG_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./install.sh"
  else
    die "依赖安装失败。请检查网络，或用 MORG_PIP_INDEX 指定可用的 pip 源后重试"
  fi
fi
ok "依赖安装完成"

# 用启动器代替 pip 入口，避免把程序再复制进 site-packages
cat > "$APP_DIR/venv/bin/morg" <<LAUNCHER
#!/bin/sh
exec env PYTHONPATH="$APP_DIR" "$APP_DIR/venv/bin/python" -m music_organizer "\$@"
LAUNCHER
chmod +x "$APP_DIR/venv/bin/morg"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$APP_DIR/venv" 2>/dev/null || true
ok "命令行启动器：$APP_DIR/venv/bin/morg"

# ---------------------------------------------------------------------------
# 4. 安装命令与 systemd 服务
# ---------------------------------------------------------------------------
BIN_LINK="/usr/local/bin/morg"
if [[ "${EUID}" -ne 0 ]]; then
  BIN_LINK="$HOME/.local/bin/morg"
  mkdir -p "$(dirname "$BIN_LINK")"
fi
ln -sfn "$APP_DIR/venv/bin/morg" "$BIN_LINK"
ok "命令行入口：$BIN_LINK"

HOME_DIR="$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6 || true)"
[[ -n "$HOME_DIR" ]] || HOME_DIR="$HOME"

if [[ "$INSTALL_SERVICE" == "1" ]]; then
  ok "已跳过 systemd 注册（MORG_NO_SERVICE=1）"
elif [[ "${EUID}" -ne 0 ]]; then
  warn "非 root 安装，跳过 systemd。启动方式：morg web --host 0.0.0.0 --port $PORT"
else
  UNIT="/etc/systemd/system/music-organizer.service"
  log "写入 systemd 服务：$UNIT"
  cat > "$UNIT" <<EOF
[Unit]
Description=Music Organizer Web UI (本地音乐整理小插件)
Documentation=file://$APP_DIR/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=HOME=$HOME_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$APP_DIR/venv/bin/morg web --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now music-organizer.service
  ok "systemd 服务已启动：music-organizer.service"

  # 可选：无 Web 的纯调度器
  AUTO_UNIT="/etc/systemd/system/music-organizer-auto.service"
  cat > "$AUTO_UNIT" <<EOF
[Unit]
Description=Music Organizer background scheduler (headless)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=HOME=$HOME_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/venv/bin/morg auto --daemon
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  ok "可选服务已写入：music-organizer-auto.service（仅在不用 Web 时启用）"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

──────────────────────────────────────────────────────────────
 安装完成 🎵
   Web 管理   http://${IP:-127.0.0.1}:${PORT}
   数据目录   $HOME_DIR/.local/share/music-organizer
   命令行     morg --help

 第一次使用：
   morg init --path $HOME_DIR/Music
   或者打开上面的 Web 地址，在弹窗里选择音乐文件夹。

 查看服务日志：
   journalctl -u music-organizer -f
──────────────────────────────────────────────────────────────
EOF
