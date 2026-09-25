#!/usr/bin/env bash
# 本地开发启动器：API、wechat-pay-worker 和前端均用宿主机进程运行。
# MySQL/Redis 需要由外部服务或本机服务预先提供。
#
# 用法：
#   ./scripts/run_local.sh                   # 启动 API + worker
#   ./scripts/run_local.sh api|worker        # 只启动其中一个服务
#   ./scripts/run_local.sh --frontends       # 额外启动 admin/h5/dealer 三个前端 dev server
#   ./scripts/run_local.sh infra             # 检查 MySQL/Redis 是否可连接
#   ./scripts/run_local.sh stop              # 停止 API/worker/前端
#   ./scripts/run_local.sh restart           # 重启 API/worker（ngrok 脚本回写 .env.ngrok 后使用）
#   ./scripts/run_local.sh status            # 查看运行状态
#   ./scripts/run_local.sh logs [api|worker|fe-admin|fe-h5|fe-dealer] [-f]
#
# 可用环境变量：
#   RUN_LOCAL_RELOAD=0   关闭 uvicorn --reload（如 .env.ngrok 联调时）
#   API_PORT=8000        覆盖 API 端口
#   NGROK_ENV_FILE       .env.ngrok 覆盖层的路径（与 run_ngrok_local.sh 保持一致）
#
# 基础 .env 由 pydantic-settings 读取；如果存在 .env.ngrok，本脚本会把它导出到进程环境覆盖 .env。

set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root_dir}"

API_PORT="${API_PORT:-8000}"
RELOAD="${RUN_LOCAL_RELOAD:-1}"
STATE_DIR="${RUN_LOCAL_STATE_DIR:-/tmp/betel_local}"
PIDS_DIR="${STATE_DIR}/pids"
LOG_DIR="${STATE_DIR}/logs"
mkdir -p "${PIDS_DIR}" "${LOG_DIR}"

api_pid_file="${PIDS_DIR}/api.pid"
worker_pid_file="${PIDS_DIR}/worker.pid"
fe_admin_pid_file="${PIDS_DIR}/fe-admin.pid"
fe_h5_pid_file="${PIDS_DIR}/fe-h5.pid"
fe_dealer_pid_file="${PIDS_DIR}/fe-dealer.pid"
api_log="${LOG_DIR}/api.log"
worker_log="${LOG_DIR}/worker.log"

log()  { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*" >&2; }
die()  { printf 'x %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

trim() { # trim STRING
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "${s}"
}

# 存在 .env.ngrok 时导出到环境，覆盖 .env（后出现的文件优先）
load_overlay() {
  local file="${NGROK_ENV_FILE:-${root_dir}/.env.ngrok}"
  [[ -f "${file}" ]] || return 0
  local line key value
  while IFS= read -r line; do
    line="$(trim "${line}")"
    [[ -n "${line}" ]] || continue
    [[ "${line}" != \#* ]] || continue
    [[ "${line}" == *=* ]] || continue
    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    case "${value}" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    export "${key}=${value}"
  done < "${file}"
  log "已加载覆盖层：${file}"
}
load_overlay

wait_http() { # wait_http URL 超时秒
  local url="$1" limit="${2:-30}" i
  for ((i = 0; i < limit; i++)); do
    curl -fsS --max-time 2 "${url}" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

check_dependencies() { # 检查外部 MySQL/Redis
  log "检查 MySQL / Redis 连接……"
  if ! ./.venv/bin/python - <<'PY'
from app.cache import check_redis
from app.db import engine
from sqlalchemy import text
from sqlmodel import Session

with Session(engine) as session:
    session.exec(text("SELECT 1")).one()
if not check_redis():
    raise RuntimeError("Redis 不可用")
print("MySQL / Redis 已就绪")
PY
  then
    die "MySQL/Redis 不可用：请先启动外部服务，并检查 DATABASE_URL / REDIS_URL。"
  fi
}

running_pid() { # running_pid PID_FILE -> 打印 pid（未运行则空）
  local pid_file="$1" pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    printf '%s' "${pid}"
  fi
}

stop_one() { # stop_one PID_FILE NAME
  local pid_file="$1" name="$2" pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    return 0
  fi
  if kill -0 "${pid}" 2>/dev/null; then
    log "停止 ${name}（pid ${pid}）……"
    pkill -TERM -P "${pid}" 2>/dev/null || true   # 先收子进程（uvicorn --reload / vite）
    kill -TERM "${pid}" 2>/dev/null || true
    local i
    for ((i = 0; i < 30; i++)); do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.2
    done
    pkill -KILL -P "${pid}" 2>/dev/null || true
    kill -KILL "${pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

start_api() {
  local pid
  pid="$(running_pid "${api_pid_file}")"
  if [[ -n "${pid}" ]]; then
    log "API 已在运行（pid ${pid}）。"
    return 0
  fi
  rm -f "${api_pid_file}"
  if [[ "${RELOAD}" == "1" ]]; then
    log "启动 API：uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT} --reload"
    nohup ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" --reload >>"${api_log}" 2>&1 &
  else
    log "启动 API：uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}"
    nohup ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${API_PORT}" >>"${api_log}" 2>&1 &
  fi
  echo $! >"${api_pid_file}"
  if wait_http "http://127.0.0.1:${API_PORT}/ready" 60; then
    log "API 就绪：http://127.0.0.1:${API_PORT}/ready"
  else
    warn "API 未在 60 秒内就绪，最近日志（${api_log}）："
    tail -n 30 "${api_log}" >&2 || true
    return 1
  fi
}

start_worker() {
  local pid
  pid="$(running_pid "${worker_pid_file}")"
  if [[ -n "${pid}" ]]; then
    log "wechat-pay-worker 已在运行（pid ${pid}）。"
    return 0
  fi
  rm -f "${worker_pid_file}"
  log "启动 wechat-pay-worker：python -m scripts.wechat_pay_worker"
  nohup ./.venv/bin/python -m scripts.wechat_pay_worker >>"${worker_log}" 2>&1 &
  echo $! >"${worker_pid_file}"
  sleep 1
  if kill -0 "$(cat "${worker_pid_file}")" 2>/dev/null; then
    log "wechat-pay-worker 已启动（pid $(cat "${worker_pid_file}")），日志：${worker_log}"
  else
    warn "wechat-pay-worker 立即退出，最近日志（${worker_log}）："
    tail -n 20 "${worker_log}" >&2 || true
    return 1
  fi
}

start_fe() { # start_fe DIR TAG PORT
  local dir="$1" tag="$2" port="$3"
  local pid_file="${PIDS_DIR}/fe-${dir}.pid"
  local log_file="${LOG_DIR}/fe-${dir}.log"
  local pid
  pid="$(running_pid "${pid_file}")"
  if [[ -n "${pid}" ]]; then
    log "frontend/${dir} 已在运行（pid ${pid}）。"
    return 0
  fi
  [[ -d "${root_dir}/frontend/${dir}/node_modules" ]] || \
    warn "frontend/${dir} 缺少 node_modules，先执行 npm --prefix frontend/${dir} install。"
  log "启动 frontend/${dir}：npm --prefix frontend/${dir} run dev（http://localhost:${port}）"
  nohup npm --prefix "${root_dir}/frontend/${dir}" run dev >>"${log_file}" 2>&1 &
  echo $! >"${pid_file}"
}

start_frontends() {
  start_fe admin admin 5173
  start_fe h5 h5 5174
  start_fe dealer dealer 5175
}

stop_all() {
  stop_one "${worker_pid_file}" "wechat-pay-worker"
  stop_one "${api_pid_file}" "API"
  stop_one "${fe_admin_pid_file}" "frontend/admin"
  stop_one "${fe_h5_pid_file}" "frontend/h5"
  stop_one "${fe_dealer_pid_file}" "frontend/dealer"
  log "本地应用进程已停止；外部 MySQL/Redis 不受影响。"
}

cmd_start() { # cmd_start [api|worker|both]
  check_dependencies
  case "${1:-both}" in
    api)    start_api ;;
    worker) start_worker ;;
    both)   start_api; start_worker ;;
    *) die "未知服务：${1:-}（可用 api | worker）" ;;
  esac
}

cmd_up() {
  cmd_start "${1:-both}"
  if [[ "${with_frontends}" == "1" ]]; then
    start_frontends
  fi
  log ""
  log "本地服务已启动：API（127.0.0.1:${API_PORT}）+ wechat-pay-worker。"
  log "启动不修改数据库结构；空库初始化或已有库升级需按部署文档手动执行。"
  log "停止：./scripts/run_local.sh stop；日志：./scripts/run_local.sh logs [api|worker|fe-admin|fe-h5|fe-dealer] [-f]"
}

cmd_restart() { # 仅重启 API/worker（不碰前端与外部依赖）
  stop_one "${worker_pid_file}" "wechat-pay-worker"
  stop_one "${api_pid_file}" "API"
  cmd_start both
}

status() {
  local spec name pid_file pid
  for spec in "API:${api_pid_file}" "wechat-pay-worker:${worker_pid_file}" "frontend/admin:${fe_admin_pid_file}" "frontend/h5:${fe_h5_pid_file}" "frontend/dealer:${fe_dealer_pid_file}"; do
    name="${spec%%:*}"
    pid_file="${spec#*:}"
    pid="$(running_pid "${pid_file}")"
    if [[ -n "${pid}" ]]; then
      log "${name}: 运行中（pid ${pid}）"
    else
      log "${name}: 未运行"
    fi
  done
  if wait_http "http://127.0.0.1:${API_PORT}/ready" 2; then
    log "API /ready: 就绪"
  else
    log "API /ready: 不可达"
  fi
}

logs() {
  local target="api" follow=0 file
  if [[ "${1:-}" == "-f" ]]; then follow=1; target="${2:-api}"
  elif [[ "${2:-}" == "-f" ]]; then follow=1; target="${1:-api}"
  else target="${1:-api}"; fi
  case "${target}" in
    api)       file="${api_log}" ;;
    worker)    file="${worker_log}" ;;
    fe-admin)  file="${LOG_DIR}/fe-admin.log" ;;
    fe-h5)     file="${LOG_DIR}/fe-h5.log" ;;
    fe-dealer) file="${LOG_DIR}/fe-dealer.log" ;;
    *) die "未知日志目标：${target}（可用 api | worker | fe-admin | fe-h5 | fe-dealer）" ;;
  esac
  [[ -f "${file}" ]] || die "没有日志文件：${file}（先启动对应服务）"
  if [[ "${follow}" == "1" ]]; then
    tail -f "${file}"
  else
    tail -n 100 "${file}"
  fi
}

with_frontends=0
positional=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontends) with_frontends=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) positional+=("$1"); shift ;;
  esac
done

cmd="${positional[0]:-up}"
case "${cmd}" in
  up)       cmd_up "${positional[@]:1}" ;;
  api)      cmd_start api ;;
  worker)   cmd_start worker ;;
  infra)    check_dependencies ;;
  stop)     stop_all ;;
  restart)  cmd_restart ;;
  status)   status ;;
  logs)     logs "${positional[@]:1}" ;;
  *) die "未知命令：${cmd}（可用 up | api | worker | infra | stop | restart | status | logs）" ;;
esac
