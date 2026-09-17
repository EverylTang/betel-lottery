#!/usr/bin/env bash
# 本地 ngrok 微信联调启动器。
#
# 拓扑：微信内置浏览器 → https://<隧道域名>/  （只暴露 H5 dev server）
#         ├── 静态页面/SPA  → 127.0.0.1:5174
#         └── /api/**       → 127.0.0.1:8000（Vite 代理）
# OAuth 回调与微信支付通知因此共用同一个公网 HTTPS 域名；
# 绝不能把 MySQL(3306)、Redis(6379)、管理后台(5173) 或 API 端口本身挂到隧道上。
#
# 用法：
#   ./scripts/run_ngrok_local.sh                 # 启动 H5 + ngrok，并自动回写 .env.ngrok
#   NGROK_DOMAIN=promo.xxx.ngrok.app ./scripts/run_ngrok_local.sh   # 使用预留固定域名
#   ./scripts/run_ngrok_local.sh check           # 只做一致性体检，不启动任何进程
#
# 可用环境变量：
#   NGROK_ENV_FILE     覆盖层 env 文件，默认仓库根 .env.ngrok（不存在时由模板生成）
#   NGROK_LOCAL_PORT   本机 H5 端口，默认 5174
#   LOCAL_API_URL      本机 API 地址，默认 http://127.0.0.1:8000
#   NGROK_SYNC_ENV=0   只打印需要改的配置，不写文件
#   NGROK_RESTART_API=0 写完配置后不自动重启本地 api / wechat-pay-worker 进程（原生进程，无需 build）
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root_dir}"

env_file="${NGROK_ENV_FILE:-${root_dir}/.env.ngrok}"
env_template="${root_dir}/.env.ngrok.example"
local_api_url="${LOCAL_API_URL:-http://127.0.0.1:8000}"
h5_port="${NGROK_LOCAL_PORT:-5174}"
ngrok_domain="${NGROK_DOMAIN:-}"
ngrok_local_api="http://127.0.0.1:4040/api/tunnels"
sync_env="${NGROK_SYNC_ENV:-1}"
restart_api="${NGROK_RESTART_API:-1}"
oauth_callback_path="/api/h5/auth/wechat/callback"
notify_path="/api/wechat-pay/notify"
# 免费 ngrok 隧道会给浏览器 UA 插入提示页；让 Vite 代理统一补 skip 头，
# API 请求（含支付回调探测）就不会被拦。设为 0 可关闭。
skip_browser_warning="${NGROK_SKIP_BROWSER_WARNING:-1}"
wechat_ua='Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN'
h5_pid=""
ngrok_pid=""
ngrok_log=""
python_bin() {
  if [[ -x "${root_dir}/.venv/bin/python" ]]; then printf '%s' "${root_dir}/.venv/bin/python"
  else printf '%s' python3; fi
}

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "${h5_pid}" ]] && kill -0 "${h5_pid}" 2>/dev/null; then
    pkill -TERM -P "${h5_pid}" 2>/dev/null || true   # npm / vite 子进程
    kill -TERM "${h5_pid}" 2>/dev/null || true
  fi
  if [[ -n "${ngrok_pid}" ]] && kill -0 "${ngrok_pid}" 2>/dev/null; then
    kill -TERM "${ngrok_pid}" 2>/dev/null || true
  fi
}

log() { printf '%s\n' "$*"; }
die() { printf 'x %s\n' "$*" >&2; exit 1; }
warn() { printf '! %s\n' "$*" >&2; }

read_env() {  # read_env KEY -> 值（覆盖层优先，其次基础 .env，都没有时返回空）
  local value
  value="$(_read_key "${env_file}" "$1")"
  # 覆盖层没这个键时回落到基础 .env，与 compose 的 env_file 优先级保持一致。
  [[ -n "${value}" ]] || value="$(_read_key "${root_dir}/.env" "$1")"
  printf '%s' "${value}"
}

_read_key() {  # _read_key FILE KEY
  [[ -f "$1" ]] || return 0
  awk -v key="$2" '
    /^[[:space:]]*#/ { next }
    { eq = index($0, "="); if (eq == 0) next
      name = substr($0, 1, eq - 1); gsub(/[[:space:]]/, "", name)
      if (name == key) { value = substr($0, eq + 1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", value); print value; exit } }
  ' "$1"
}

write_env() {  # write_env KEY VALUE —— 覆盖同名键（含被注释掉的键），缺失则追加
  ENV_KEY="$1" ENV_VALUE="$2" ENV_TARGET="${env_file}" "$(python_bin)" - <<'PYEOF'
import os, pathlib
key, value, path = os.environ["ENV_KEY"], os.environ["ENV_VALUE"], pathlib.Path(os.environ["ENV_TARGET"])
lines = path.read_text().splitlines()
for index, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith(key + "=") or stripped.startswith("# " + key + "="):
        lines[index] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines).rstrip("\n") + "\n")
PYEOF
}

wait_http() {  # wait_http URL 超时秒数
  local url="$1" limit="${2:-30}" i
  for ((i = 0; i < limit; i++)); do
    curl -fsS --max-time 2 "${url}" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

base_env_value() {  # base_env_value KEY -> 从仓库根 .env 读取（缺失返回空）
  [[ -f "${root_dir}/.env" ]] || return 0
  awk -v key="$1" '
    /^[[:space:]]*#/ { next }
    { eq = index($0, "="); if (eq == 0) next
      name = substr($0, 1, eq - 1); gsub(/[[:space:]]/, "", name)
      if (name == key) { value = substr($0, eq + 1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", value); print value; exit } }
  ' "${root_dir}/.env"
}

preflight() {
  command -v ngrok >/dev/null 2>&1 || die "未安装 ngrok（需要 3.x）。brew install ngrok 后执行 ngrok config add-authtoken <token>。"
  ngrok config check >/dev/null 2>&1 || die "ngrok 配置无效，请先执行 ngrok config add-authtoken <token>。"
  if [[ ! -f "${env_file}" ]]; then
    [[ -f "${env_template}" ]] || die "缺少 ${env_file}，也没有 ${env_template} 可复制。"
    cp "${env_template}" "${env_file}"
    warn "已生成 ${env_file}（已在 .gitignore 内）。隧道域名由脚本自动写入；公众号 AppID/AppSecret 仍需手填。"
  fi
  local environment
  environment="$(read_env ENVIRONMENT)"
  if [[ "${environment}" == "production" ]]; then
    die "ENVIRONMENT=production 会触发生产强校验（真实 HTTPS 域名 + 商户证书）。ngrok 联调请用 staging。"
  fi
  if [[ -z "${environment}" ]]; then
    warn "${env_file} 未设置 ENVIRONMENT，建议 staging。"
  fi
  case "$(read_env ADMIN_BOOTSTRAP_PASSWORD)" in
    ""|change-me-now) die "ADMIN_BOOTSTRAP_PASSWORD 仍是默认值：隧道会把 /api/admin/auth/login 暴露到公网（限流 10 次/5 分钟），请先在 ${env_file} 换成强口令。" ;;
  esac
  if [[ "$(read_env WECHATPAY_ENABLED)" == "true" ]]; then
    # 与 compose 的 ${WECHATPAY_SECRET_DIR:-./secrets/wechatpay} 保持一致：
    # 先看 shell 环境变量，再看根目录 .env（compose 插值只读 .env，脚本不会自动 source 它）。
    local secret_dir="${WECHATPAY_SECRET_DIR:-$(base_env_value WECHATPAY_SECRET_DIR)}"
    [[ -n "${secret_dir}" ]] || secret_dir="./secrets/wechatpay"
    local key_dir
    case "${secret_dir}" in
      /*) key_dir="${secret_dir}" ;;
      *)  key_dir="${root_dir}/${secret_dir#./}" ;;
    esac
    [[ -s "${key_dir}/apiclient_key.pem" ]] || warn "WECHATPAY_ENABLED=true 但 ${key_dir}/apiclient_key.pem 缺失，创建转账单会失败。"
    [[ -s "${key_dir}/wechatpay_public_key.pem" ]] || warn "WECHATPAY_ENABLED=true 但 ${key_dir}/wechatpay_public_key.pem 缺失，回调验签会失败。"
  fi
  if ! ls frontend/h5/public/MP_verify_*.txt >/dev/null 2>&1; then
    warn "frontend/h5/public/ 下没有 MP_verify_*.txt：微信保存「网页授权域名/JS 接口安全域名」时会访问 https://<域名>/MP_verify_xxx.txt 校验归属，请先从公众号后台下载放入该目录。"
  fi
}

report() {
  local base domain
  base="$(read_env H5_BASE_URL)"
  domain="${base#*://}"
  log "覆盖层文件            : ${env_file}"
  log "H5_BASE_URL           : ${base}"
  log "WECHAT_OAUTH_REDIRECT : $(read_env WECHAT_OAUTH_REDIRECT_URI)"
  log "WECHATPAY_NOTIFY_BASE : $(read_env WECHATPAY_NOTIFY_BASE_URL)"
  log "TRUSTED_HOSTS         : $(read_env TRUSTED_HOSTS)"
  log "CORS_ORIGINS          : $(read_env CORS_ORIGINS)"
  log "ENVIRONMENT           : $(read_env ENVIRONMENT) / WECHATPAY_ENABLED=$(read_env WECHATPAY_ENABLED)"
  log ""
  log "微信公众号后台要填的域名（不带协议/端口/路径）：${domain}"
  log "  功能设置 → 网页授权域名        ${domain}"
  log "  功能设置 → JS 接口安全域名     ${domain}"
  log "  商户平台 → 商家转账通知地址    https://${domain}${notify_path}"
}

check_only() {
  preflight
  local base domain
  base="$(read_env H5_BASE_URL)"
  [[ "${base}" == https://* ]] || die "H5_BASE_URL 必须是 https:// 开头的公网隧道地址，当前：${base:-空}"
  report
  domain="${base#*://}"
  if wait_http "${local_api_url}/ready" 3; then
    log "本机 API             : 就绪（${local_api_url}）"
  else
    warn "本机 API 未就绪：先执行 ./scripts/run_local.sh（MySQL/Redis 用 docker，其余原生运行）"
  fi
  if curl -fsS --max-time 5 -A "${wechat_ua}" -H 'Accept: application/json' \
       -H 'ngrok-skip-browser-warning: 1' "https://${domain}/api/h5/auth/mode" 2>/dev/null | grep -q 'oauth_enabled'; then
    log "隧道 → API           : 可达（https://${domain}/api/h5/auth/mode）"
  else
    warn "https://${domain}/api/h5/auth/mode 不可达：隧道未启动，或 Vite 的 /api 代理目标不对。"
  fi
}

inspect_tunnels() {  # 从 ngrok 本地 API 读取真实隧道状态：URL<TAB>状态
  local api="${1:-${ngrok_local_api}}"
  NGROK_LOCAL_API="${api}" "$(python_bin)" - <<'PYEOF'
import json, os, urllib.error, urllib.request
url = os.environ["NGROK_LOCAL_API"]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        payload = json.load(response)
except (urllib.error.URLError, OSError, ValueError):
    raise SystemExit(1)
rows = []
for tunnel in payload.get("tunnels", []):
    public = tunnel.get("public_url") or ""
    # ngrok 3.x 的 /api/tunnels 对已就绪隧道可能不带 status 字段，缺失时视为 up。
    status = str(tunnel.get("status") or "up")
    if public.startswith("https://"):
        rows.append((public, status))
if rows:
    rows.sort(key=lambda row: (row[1] != "up", row[0]))
    print("\t".join(rows[0]))
PYEOF
}

start_tunnel() {  # 输出 public_url<TAB>状态；NGROK_DRY_RUN=1 用于离线自测
  local api="${NGROK_INSPECT_API:-${ngrok_local_api}}"
  if [[ "${NGROK_DRY_RUN:-0}" == "1" ]]; then
    printf '%s\t%s\n' "https://dry-run.ngrok-free.app" "up"
    return 0
  fi
  local args=(http "${h5_port}" "--log=stdout" "--log-format=logfmt" "--log-level=info")
  if [[ -n "${ngrok_domain}" ]]; then
    args=(http "--domain=${ngrok_domain}" "${h5_port}" "--log=stdout" "--log-format=logfmt" "--log-level=info")
  fi
  ngrok_log="$(mktemp -t betel-ngrok)"
  ngrok "${args[@]}" >"${ngrok_log}" 2>&1 &
  ngrok_pid=$!
  local i line url state
  for ((i = 0; i < 45; i++)); do
    if ! kill -0 "${ngrok_pid}" 2>/dev/null; then
      warn "ngrok 进程已退出，最近日志："
      tail -n 5 "${ngrok_log}" >&2 || true
      return 1
    fi
    if line="$(inspect_tunnels "${api}")" && [[ -n "${line}" ]]; then
      url="${line%%$'\t'*}"; state="${line##*$'\t'}"
      if [[ "${state}" == "up" ]]; then
        printf '%s\t%s\n' "${url}" "${state}"
        return 0
      fi
    fi
    sleep 1
  done
  warn "等待 ngrok 隧道就绪超时（45 秒），最近日志："
  tail -n 5 "${ngrok_log}" >&2 || true
  return 1
}

sync_config() {  # 把隧道域名写进覆盖层 env
  local url="$1" domain="$2" origins trusted
  origins="https://${domain},http://localhost:5173,http://localhost:5174,http://localhost:5175"
  trusted="${domain},localhost,127.0.0.1"
  if [[ "${sync_env}" != "1" ]]; then
    log "NGROK_SYNC_ENV=0：请手动把下列值写入 ${env_file}"
    log "  H5_BASE_URL=${url}"
    log "  WECHAT_OAUTH_REDIRECT_URI=${url}${oauth_callback_path}"
    log "  WECHATPAY_NOTIFY_BASE_URL=${url}"
    log "  TRUSTED_HOSTS=${trusted}"
    log "  CORS_ORIGINS=${origins}"
    return 0
  fi
  write_env H5_BASE_URL "${url}"
  write_env WECHAT_OAUTH_REDIRECT_URI "${url}${oauth_callback_path}"
  write_env WECHATPAY_NOTIFY_BASE_URL "${url}"
  write_env TRUSTED_HOSTS "${trusted}"
  write_env CORS_ORIGINS "${origins}"
  log "已写入 ${env_file}：H5_BASE_URL / WECHAT_OAUTH_REDIRECT_URI / WECHATPAY_NOTIFY_BASE_URL / TRUSTED_HOSTS / CORS_ORIGINS"
}

restart_services() {
  local runner="${root_dir}/scripts/run_local.sh"
  if [[ "${restart_api}" != "1" ]]; then
    warn "NGROK_RESTART_API=0：改完配置后需自行执行 ${runner} restart"
    return 0
  fi
  if [[ ! -x "${runner}" ]]; then
    warn "缺少 ${runner}，请手动重启 API / worker 使新域名生效。"
    return 0
  fi
  # API 与 worker 是 .venv 原生进程（MySQL/Redis 才是容器）：run_local.sh 会重新加载
  # .env 与 .env.ngrok 覆盖层，无需 build 镜像。
  log "重启本地 api / wechat-pay-worker（原生进程）以加载新配置……"
  if ! "${runner}" restart; then
    die "本地服务重启失败，见上方输出与 ${root_dir}/scripts/run_local.sh logs。"
  fi
  if wait_http "${local_api_url}/ready" 120; then
    log "API 已就绪：${local_api_url}/ready"
  else
    warn "API 在 120 秒内未就绪，执行 ./scripts/run_local.sh logs api 查看原因（多为配置校验失败）。"
  fi
}

smoke_test() {
  local domain="$1" code
  if curl -fsS --max-time 8 "https://${domain}/" | grep -qi "<div id=\"app\""; then
    log "H5 页面          : https://${domain}/ 可访问"
  else
    warn "H5 页面自检未通过：微信内打开可能被 ngrok 免费套餐的提示页拦截，需在浏览器点一次 Visit Site 或改用固定域名/付费套餐。"
  fi
  if curl -fsS --max-time 8 "https://${domain}/api/h5/auth/mode" | grep -q 'oauth_enabled'; then
    log "隧道 → API       : 可达（/api/h5/auth/mode）"
  else
    warn "https://${domain}/api/h5/auth/mode 不可达：检查 Vite 是否把 /api 代理到 ${local_api_url}。"
  fi
  if curl -s --max-time 8 -A "${wechat_ua}" -H 'Accept: text/html' "https://${domain}/" | grep -q 'ERR_NGROK_6024'; then
    warn "免费隧道正在向浏览器 UA 插入 ngrok 提示页：微信首次打开 ${url} 需点一次 Visit Site（写入 cookie 后放行），
  之后页面内的 /api 请求由 Vite 代理自动带 ngrok-skip-browser-warning 头，不再被拦。
  要彻底避免，请升级为付费/预留域名，或改用自有 HTTPS 服务器。"
  fi
  code="$(curl -s --max-time 8 -o /dev/null -w '%{http_code}' -X POST "https://${domain}/api/wechat-pay/notify")"
  case "${code}" in
    400|401|403|503) log "支付回调入口     : POST ${notify_path} 返回 HTTP ${code}（公网可达；503=未配置商户参数，属预期）" ;;
    000) warn "支付回调入口     : 请求未送达隧道" ;;
    *)   log "支付回调入口     : POST /api/wechat-pay/notify 返回 HTTP ${code}" ;;
  esac
  code="$(curl -s --max-time 8 -o /dev/null -w '%{http_code}' "https://${domain}/api/h5/auth/mode")"
  log "授权模式接口     : GET /api/h5/auth/mode 返回 HTTP ${code}（oauth_enabled=true 表示已启用真实公众号 OAuth）"
}

run_default() {
  preflight
  wait_http "${local_api_url}/ready" 3 || die "本机 API 未就绪：${local_api_url}/ready。请先执行 ./scripts/run_local.sh。"
  ( cd "${root_dir}/frontend" && VITE_API_PROXY_TARGET="${local_api_url}" \
      VITE_NGROK_SKIP_BROWSER_WARNING="${skip_browser_warning}" \
      npm run dev -w h5 -- --host 0.0.0.0 --port "${h5_port}" ) &
  h5_pid=$!
  trap 'cleanup' EXIT INT TERM
  if [[ "${NGROK_DRY_RUN:-0}" != "1" ]]; then
    if ! wait_http "http://127.0.0.1:${h5_port}/" 40; then
      die "H5 dev server 未能在 40 秒内监听 ${h5_port}。"
    fi
  fi
  local line url domain
  if ! line="$(start_tunnel)"; then
    die "ngrok 隧道未就绪。请检查 authtoken、固定域名 ${ngrok_domain:-（未指定，使用临时域名）}，以及本机 4040 检查端口是否被其他 ngrok 实例占用。"
  fi
  url="${line%%$'\t'*}"
  domain="${url#*://}"
  log ""
  log "公网隧道地址：${url}"
  log ""
  sync_config "${url}" "${domain}"
  restart_services
  report
  smoke_test "${domain}"
  log ""
  if [[ "${ngrok_domain}" != "${domain}" ]]; then
    warn "当前是临时域名：重启后会变化，必须同步公众号后台与 ${env_file}。建议 ngrok 预留固定域名后用 NGROK_DOMAIN=... 启动。"
  fi
  log "验证顺序：① 手机浏览器打开 ${url} 走抽奖；② 微信内打开走 OAuth；③ 再开 WECHATPAY_ENABLED=true 做小额打款。"
  log "停止：Ctrl-C（本脚本会同时结束 H5 与 ngrok 隧道）。"
  wait
}

case "${1:-up}" in
  check) check_only ;;
  up)
    if [[ $# -gt 1 ]]; then die "未知参数：$2"; fi
    run_default
    ;;
  *) die "用法：$0 [up|check]" ;;
esac
