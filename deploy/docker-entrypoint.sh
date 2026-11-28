#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-serve}"
DATA_DIR="${COMPANION_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"

# 逐行加载挂载进来的 .env（不走 shell source/Compose 插值，避免 PBKDF2 哈希里的 $ 和含空格值被破坏）
load_env_file() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
      *=*)
        local k="${line%%=*}"
        local v="${line#*=}"
        # 已通过 docker compose environment 显式覆盖的变量优先保留
        if [ -n "$k" ] && [ -z "${!k+x}" ]; then
          export "${k}=${v}"
        fi
        ;;
    esac
  done < "$env_file"
}

PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait || true
}
trap cleanup TERM INT

if [ "$MODE" = "demo" ]; then
  if [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
    python3 tools/demo_data.py "$DATA_DIR"
  fi
  python3 -u tools/demo_bridge.py --scene "${DEMO_SCENE:-ok}" &
  PIDS+=("$!")
  python3 -u server.py &
  PIDS+=("$!")
  set +e
  wait -n "${PIDS[@]}"
  STATUS=$?
  set -e
  cleanup
  exit "$STATUS"
fi

if [ "$MODE" = "serve" ]; then
  load_env_file "/app/.env"
  if [ ! -w "$DATA_DIR" ]; then
    echo "[wozaine] 错误：数据目录 $DATA_DIR 不可写，请在宿主机执行 mkdir -p data public && chown 1000:1000 data public" >&2
    exit 1
  fi
  if [ -z "${ADMIN_PASSWORD_HASH:-}" ] || [ -z "${ADMIN_SESSION_SECRET:-}" ]; then
    echo "[wozaine] 提示：未设置 ADMIN_PASSWORD_HASH / ADMIN_SESSION_SECRET，公网暴露前请先运行 admin_password.py 设置管理台密码。" >&2
  fi
  if [ -z "${XIAOZHI_CTL_TOKEN:-}" ]; then
    echo "[wozaine] 提示：未设置 XIAOZHI_CTL_TOKEN，管理台将无法访问桥接控制口（请先在 .env 中设置随机口令）。" >&2
  fi
  python3 -u xiaozhi_bridge.py &
  PIDS+=("$!")
  python3 -u realtime_server.py &
  PIDS+=("$!")
  python3 -u server.py &
  PIDS+=("$!")
  set +e
  wait -n "${PIDS[@]}"
  STATUS=$?
  set -e
  cleanup
  exit "$STATUS"
fi

load_env_file "/app/.env"
exec "$@"
