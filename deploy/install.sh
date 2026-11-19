#!/usr/bin/env bash
# =============================================================================
# 我在呢 · 一键安装 / 更新（Ubuntu 22.04+ / Debian 12+）
#
# 用法（在仓库目录里，用「以后跑服务的那个普通用户」执行，需要 sudo 权限）：
#   bash deploy/install.sh --domain chat.example.com --email you@example.com
#   bash deploy/install.sh --no-nginx          # 只装服务，不配 nginx/证书（自己有反向代理时，
#                                              #  必须照 deploy/nginx.conf.template 给 /ws/call、/ws/intercom 配 auth_request）
# 幂等：改了代码或配置后再跑一遍就是「更新并重启」。
# =============================================================================
set -euo pipefail

DOMAIN=""; EMAIL=""; WITH_NGINX=1; PIP_INDEX="${PIP_INDEX:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --email) EMAIL="$2"; shift 2 ;;
        --no-nginx) WITH_NGINX=0; shift ;;
        --pip-index) PIP_INDEX="$2"; shift 2 ;;   # 国内可用 https://pypi.tuna.tsinghua.edu.cn/simple
        -h|--help) sed -n 2,10p "$0"; exit 0 ;;
        *) echo "未知参数：$1"; exit 2 ;;
    esac
done

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(id -un)"
cd "$APP_DIR"
say() { printf '\n\033[1;32m[install]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[install] %s\033[0m\n' "$*"; exit 1; }
[ "$APP_USER" != root ] || die "请用普通用户运行（服务会以这个用户身份跑），不要用 root。"
sudo -v || die "需要 sudo 权限。"

# ---- 0. 先检查配置文件（缺东西就别白装半天依赖）----
mkdir -p data public
[ -f family.json ] || { cp family.example.json family.json; say "已生成 family.json —— 记得改成你家的信息"; }
[ -f persona.md ] || { cp persona.example.md persona.md; say "已生成 persona.md —— 记得把【方括号】换成你家的真实情况"; }
if [ ! -f .env ]; then
    cp .env.example .env; chmod 600 .env
    die "已生成 .env。请先填好里面标「必填」的密钥和域名，再重新运行本脚本。"
fi
chmod 600 .env
getenv() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
setenv() { if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi; }

missing=""
for k in DEEPSEEK_API_KEY MINIMAX_API_KEY MINIMAX_VOICE_ID; do [ -n "$(getenv $k)" ] || missing="$missing $k"; done
[ -n "$(getenv DASHSCOPE_API_KEY)$(getenv TENCENT_SECRET_ID)" ] || missing="$missing DASHSCOPE_API_KEY(或TENCENT_SECRET_ID/KEY)"
[ -z "$missing" ] || die ".env 还缺：$missing"
if [ -n "$DOMAIN" ]; then
    setenv XIAOZHI_PUBLIC_WS_URL "wss://$DOMAIN/xiaozhi/ws"
    setenv XIAOZHI_PUBLIC_OTA_URL "https://$DOMAIN/xiaozhi/ota/"
fi
case "$(getenv XIAOZHI_PUBLIC_WS_URL)" in *YOUR_DOMAIN*|"") die "请用 --domain 指定域名，或在 .env 里填 XIAOZHI_PUBLIC_WS_URL / XIAOZHI_PUBLIC_OTA_URL";; esac
# ---- 1. 系统依赖 ----
say "安装系统依赖（python3-venv、ffmpeg、libopus、nginx、certbot）"
PKGS="python3 python3-venv python3-pip ffmpeg libopus0 curl openssl"
[ "$WITH_NGINX" = 1 ] && PKGS="$PKGS nginx certbot python3-certbot-nginx"
sudo apt-get update -qq
# shellcheck disable=SC2086
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS >/dev/null

# ---- 2. Python 环境 ----
if [ ! -x venv/bin/python3 ]; then say "创建 venv"; python3 -m venv venv; fi
if [ ! -f venv/.requirements.sha ] || [ "$(sha256sum requirements.txt | cut -d' ' -f1)" != "$(cat venv/.requirements.sha)" ]; then
    say "安装 Python 依赖"
    venv/bin/pip install -q --upgrade pip ${PIP_INDEX:+-i "$PIP_INDEX"}
    venv/bin/pip install -q -r requirements.txt ${PIP_INDEX:+-i "$PIP_INDEX"}
    sha256sum requirements.txt | cut -d' ' -f1 > venv/.requirements.sha
fi

# ---- 3. 自动生成口令、设管理台密码 ----
for k in XIAOZHI_CTL_TOKEN XIAOZHI_DEVICE_TOKEN; do
    [ -n "$(getenv $k)" ] || { setenv $k "$(openssl rand -hex 24)"; say "已自动生成 $k"; }
done
if [ -z "$(getenv ADMIN_PASSWORD_HASH)" ]; then
    say "设置管理台登录密码（至少 8 位；用户名是 .env 里的 ADMIN_USER，默认 admin）"
    venv/bin/python3 admin_password.py || die "密码没设成功（至少 8 位），重新运行本脚本即可。"
fi

# ---- 4. 离线自检（不花钱，不调模型）----
say "离线自检"
venv/bin/python3 tests/test_typo_book.py >/dev/null || die "tests/test_typo_book.py 没通过"
venv/bin/python3 -c "import family, live_context, story, book, deepseek_client, asr_client, tts_client, vad" || die "模块导入失败"

# ---- 5. 管理台「重启服务」用的受限 sudo ----
sudo install -m 755 -o root -g root deploy/wozaine-restart /usr/local/sbin/wozaine-restart
echo "$APP_USER ALL=(root) NOPASSWD: /usr/local/sbin/wozaine-restart" | sudo tee /etc/sudoers.d/wozaine >/dev/null
sudo chmod 440 /etc/sudoers.d/wozaine
sudo visudo -cf /etc/sudoers.d/wozaine >/dev/null || { sudo rm -f /etc/sudoers.d/wozaine; die "sudoers 校验失败"; }

# ---- 6. systemd ----
say "安装 systemd 服务"
UNITS="companion.service companion-realtime.service xiaozhi-bridge.service daily-report.service daily-report.timer
       voice-keepalive.service voice-keepalive.timer device-monitor.service device-monitor.timer
       schedule-sync.service schedule-sync.timer"
for u in $UNITS; do
    sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" "systemd/$u" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable -q companion.service companion-realtime.service xiaozhi-bridge.service \
    daily-report.timer voice-keepalive.timer device-monitor.timer
[ -n "$(getenv SCHEDULE_ICS_URL)" ] && sudo systemctl enable -q --now schedule-sync.timer
sudo systemctl restart companion.service companion-realtime.service
sudo systemctl restart xiaozhi-bridge.service
sudo systemctl start daily-report.timer voice-keepalive.timer device-monitor.timer

# ---- 7. nginx + HTTPS ----
if [ "$WITH_NGINX" = 1 ]; then
    [ -n "$DOMAIN" ] || DOMAIN="$(getenv XIAOZHI_PUBLIC_OTA_URL | sed -E 's#https?://([^/]+)/.*#\1#')"
    say "配置 nginx：$DOMAIN"
    CONF=/etc/nginx/sites-available/wozaine.conf
    if [ ! -f "$CONF" ]; then
        sed "s#__DOMAIN__#$DOMAIN#g" deploy/nginx.conf.template | sudo tee "$CONF" >/dev/null
        sudo ln -sf "$CONF" /etc/nginx/sites-enabled/wozaine.conf
        sudo nginx -t && sudo systemctl reload nginx
        say "申请 HTTPS 证书（域名要已经解析到这台服务器，80/443 端口要放行）"
        if [ -n "$EMAIL" ]; then
            sudo certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --redirect -n || say "!! 证书申请失败，修好 DNS/端口后执行：sudo certbot --nginx -d $DOMAIN"
        else
            sudo certbot --nginx -d "$DOMAIN" --redirect || say "!! 证书申请失败，修好 DNS/端口后执行：sudo certbot --nginx -d $DOMAIN"
        fi
    else
        say "$CONF 已存在，不覆盖（要重装就先删掉它）"
    fi
fi

# ---- 8. 健康检查 ----
sleep 4; ok=1
for u in companion.service companion-realtime.service xiaozhi-bridge.service; do
    st=$(systemctl is-active "$u" || true); echo "[health] $u: $st"; [ "$st" = active ] || ok=0
done
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:3096/xiaozhi/ota/ || true)
echo "[health] 设备 OTA 接口: HTTP $code"; [ "$code" = 200 ] || ok=0
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3093/login || true)
echo "[health] 管理台: HTTP $code"; [ "$code" = 200 ] || ok=0
if [ "$ok" = 1 ]; then
    say "全部正常。管理台：https://${DOMAIN:-你的域名}/  设备 OTA 地址：$(getenv XIAOZHI_PUBLIC_OTA_URL)"
    say "下一步：给设备刷固件并把 OTA 地址设成上面这个（见 firmware/README.md），然后看 docs/使用说明.md"
    if [ -z "$(getenv XIAOZHI_ALLOWED_DEVICE_IDS)" ] || [ "$(getenv XIAOZHI_REQUIRE_DEVICE_TOKEN)" != 1 ]; then
        printf '\n\033[1;33m[注意] 现在任何知道你域名的设备都能连上来、消耗你的接口额度。\n设备一连上，就把它的 MAC 填进 .env 的 XIAOZHI_ALLOWED_DEVICE_IDS，并设 XIAOZHI_REQUIRE_DEVICE_TOKEN=1，再运行一次本脚本。\033[0m\n'
    fi
else
    die "有服务没起来：journalctl -u xiaozhi-bridge -u companion -n 100 --no-pager 看看报错"
fi
