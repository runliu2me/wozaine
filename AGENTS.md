# AGENTS.md —— 给 AI 助手的部署说明

> 你（AI 助手）正在帮一位用户把「我在呢」部署起来：一台给老人用的 AI 陪聊设备，用用户自己克隆的声音陪家里老人聊天。
> 用户多半不是程序员。请按下面的阶段推进，**每个阶段结束都跟用户确认一次**，不要一口气猜完所有配置。
> 详细的人类版说明在 [docs/部署.md](docs/部署.md)，本文件是它的执行清单；两者冲突时以真实代码为准。

## 0. 先读懂这个仓库

| 路径 | 是什么 |
|---|---|
| `xiaozhi_bridge.py` | 核心服务。实现小智（xiaozhi-esp32）设备协议：OTA（:3096）、设备 WebSocket（:3097）、对讲（:3098）。收音 → `vad.py` 断句 → `asr_client.py` 识别 → `deepseek_client.py` 生成回复 → `tts_client.py` 合成 → Opus 发回设备 |
| `server.py` + `web/` | 手机管理台（:3093），网页登录、家人账号、对话记录、日报、记忆、故事、错字本、维护 |
| `realtime_server.py` | 管理台「通话」页的浏览器全双工语音（:3095），可选 |
| `daily_report.py` | 每晚任务：日报、整理记忆、摘故事（`story.py`）、写书（`book.py`）、错字本回改（`typo_book.py`） |
| `device_monitor.py` | 每 15 分钟检查设备离线 / 服务故障 / 余额，发 webhook 告警 |
| `family.py` + `family.example.json` | 家庭档案：老人和孩子的名字、城市、书名、识别提示词。代码里不写死任何人名地名 |
| `persona.example.md` | 人设模板，复制成 `persona.md`，每轮对话原样放进 system prompt |
| `story_seed.json` | 故事题库种子（章节 + 问题） |
| `typo_seed.example.json` | 错字本第一批词（可选） |
| `.env.example` | 全部配置项和说明 |
| `deploy/install.sh` | 一键安装 / 更新脚本（Ubuntu/Debian，幂等） |
| `deploy/nginx.conf.template` | nginx 站点模板 |
| `systemd/` | 服务单元模板（`__APP_DIR__` `__APP_USER__` 由安装脚本替换） |
| `firmware/` | 小智固件的补丁和编译烧录说明 |
| `tests/test_typo_book.py` | 离线测试，不花钱，任何改动后都可以跑 |
| `tests/run_scenarios.py` | 桥接服务的场景测试，会真实调用云服务（几分钱），需要 `.env` 和至少一段录音 |

## 铁律

1. **绝不提交、打印、上传用户的秘密和隐私**：`.env`、`family.json`、`persona.md`、`typo_seed.json`、`data/` 都在 `.gitignore` 里，保持这样。
   在对话里展示配置时把密钥打码。
2. **密钥一律问用户要**，不要编造、不要用占位值假装配好了。用户还没有的，告诉他去哪儿申请（见下面阶段 1）。
3. **管理台对公网开放前必须设好登录密码**（`install.sh` 会强制）。用 `--no-nginx` 自己做反代时，必须照 `deploy/nginx.conf.template` 给 `/ws/call`、`/ws/intercom` 配 `auth_request /_auth`，否则这两条语音通道没有鉴权。不要为了省事关掉鉴权或把服务端口直接暴露到公网——
   所有服务只监听 127.0.0.1，公网只经过 nginx 的 443。
4. **不要替用户克隆别人的声音。** 只能用用户本人（或明确同意的家人）的录音。
5. 在用户的服务器上执行有副作用的命令（装软件、改 nginx、申请证书、重启服务）之前，先说一句要做什么。
6. 部署完成前，按第 6 节的验收清单逐项确认，不要只说「应该可以了」。

## 1. 收集信息（先问清楚再动手）

一次性问用户这些问题（用户不懂的就解释一下，给默认建议）：

- 设备给谁用？怎么称呼（妈 / 爸）？大概多大年纪、哪里人、说不说方言？
- AI 用谁的身份和声音说话（用户本人的名字）？你们各自住在哪个城市？
- 有没有服务器？（没有的话建议：Ubuntu 22.04/24.04，2 核 4G，有公网 IP，放行 80/443；
  大陆服务器需要已备案域名，否则建议香港 / 海外服务器）
- 有没有域名？打算用哪个子域名？（需要先把 A 记录解析到服务器 IP）
- 已经有哪些账号：DeepSeek、阿里云百炼、腾讯云、MiniMax？
- 有没有设备？是哪块板子？（见 [docs/硬件.md](docs/硬件.md)。我们的固件/ROM 基于征辰 Minicam，但不是推荐购买；其他板子可改 sdkconfig 或让 AI 按 firmware/README 改编）
- 告警想发到哪里：企业微信群、飞书群、钉钉群，还是邮件？
- （可选）有没有能导出 iCal 订阅链接的日历，想让 AI 知道你最近的行程？

## 2. 申请密钥、克隆声音

带用户完成 [docs/部署.md](docs/部署.md) 第 1、2 节。最少需要：

- `DEEPSEEK_API_KEY`
- `DASHSCOPE_API_KEY`（阿里云百炼）或 `TENCENT_SECRET_ID` + `TENCENT_SECRET_KEY`，建议两个都配
- `MINIMAX_API_KEY`、`MINIMAX_GROUP_ID`、`MINIMAX_VOICE_ID`

声音复刻要点：用户本人 1～3 分钟**自然聊天**的录音，不要念稿；在 MiniMax 控制台或按其官方接口文档做快速复刻。
还没复刻好可以先用 `MINIMAX_VOICE_ID=male-qn-qingse` 跑通。

## 3. 准备服务器

- SSH 登录后确认系统：`lsb_release -a`（Ubuntu 22.04+ / Debian 12+；其他发行版需要自己改 `install.sh` 的包管理命令）。
- 确认有一个能 sudo 的普通用户；没有就建一个，后面都用它操作。
- 确认域名解析：`dig +short 子域名` 等于服务器公网 IP；安全组 / 防火墙放行 80、443。
- `git clone https://github.com/runliu2me/wozaine.git && cd wozaine`

## 4. 写配置

1. `cp .env.example .env && chmod 600 .env`，按用户给的值填写。必填：大模型、识别、合成三组密钥。
   `XIAOZHI_PUBLIC_WS_URL` / `XIAOZHI_PUBLIC_OTA_URL` 可以留给安装脚本的 `--domain` 自动填。
   有告警渠道就填 `ALERT_WEBHOOK_URL` 和 `ALERT_WEBHOOK_KIND`。
2. `cp family.example.json family.json`，按阶段 1 的回答填：`elder_name`、`elder_brief`（如「成都人，七十多岁，说四川话」）、
   `child_name`、`child_nickname`、两个城市的名字和经纬度、`book_title`、`asr_context`（写上家里常提的人名）、`asr_hotwords`。
3. `cp persona.example.md persona.md`，**和用户一起**把【方括号】填成真实情况。这一步最影响效果：
   问用户平时怎么跟老人说话、家里有谁、老人身体和作息、每周固定做什么、多久回家一次。
   不需要的段落删掉。不要写用户不愿意交给大模型服务商的隐私。
4. 如果是陪爸爸：把 `persona.md` 里的称呼全部换成爸爸，并按 [docs/定制指南.md](docs/定制指南.md)「陪爸爸」一节改代码里的开场白。
5. （可选）`story_seed.json` 按老人的经历调整章节和问题；有已知容易被听错的人名，写一个 `typo_seed.json`。

## 5. 安装

```bash
bash deploy/install.sh --domain <子域名> --email <用户邮箱>
# 国内服务器 pip 慢可以加：--pip-index https://pypi.tuna.tsinghua.edu.cn/simple
```

- 脚本第一次运行如果发现没有 `.env` 会生成后退出，填好再跑。
- 会交互式要求设置管理台密码（`admin_password.py`），让用户自己输入，不要替他设。
- 证书申请失败通常是 DNS 没生效、80 端口没放行或大陆服务器没备案；修好后 `sudo certbot --nginx -d <子域名>`。
- 任何服务没起来：`journalctl -u xiaozhi-bridge -u companion -n 100 --no-pager`。

然后按 [firmware/README.md](firmware/README.md) 带用户刷固件并把 OTA 地址设为 `https://<子域名>/xiaozhi/ota/`。

## 6. 验收清单（逐项确认）

- [ ] `systemctl is-active xiaozhi-bridge companion companion-realtime` 全部 active
- [ ] 浏览器打开 `https://<子域名>/` 跳到登录页，用户能用自己设的密码登录
- [ ] 管理台「维护 → 连通性」里 DeepSeek / 识别 / 合成都正常；「测试对话」能收到回复并听到用户的声音
- [ ] 设备开机后连上 Wi-Fi，`journalctl -u xiaozhi-bridge` 出现它的连接记录和 MAC
- [ ] 拍一下设备，听到「妈，我在呢……」，说一句话能得到回复；管理台「记录」里出现这句话和原声
- [ ] **尽快**把设备 MAC 写进 `XIAOZHI_ALLOWED_DEVICE_IDS` 和 `XIAOZHI_ELDER_DEVICE_ID`（喇叭是 24k 的板子再加 `XIAOZHI_TX_SAMPLE_RATES=<MAC>=24000`，看板子 config.h 的 `AUDIO_OUTPUT_SAMPLE_RATE`），`XIAOZHI_REQUIRE_DEVICE_TOKEN=1`，重跑 `install.sh` 后设备仍能对话
- [ ] 告警渠道收到过一条测试消息（`venv/bin/python3 -c "import device_monitor; device_monitor.notify('我在呢：告警测试')"`，device_monitor 会自己读 .env）
- [ ] 提醒用户：`data/` 是全部数据，需要定期备份；`.env` 不要发给任何人

## 7. 部署之后

把 [docs/使用说明.md](docs/使用说明.md) 介绍给用户：怎么教老人用、管理台每一页干什么、第一周要多看「记录」调人设、
一两周后在「错字本」里确认常被听错的名字。

## 修改代码时

- 先跑 `venv/bin/python3 tests/test_typo_book.py`（离线、免费）。
- 对话链路的改动用 `tests/run_scenarios.py` 验证（会花几分钱，它会在另一组端口起一个独立的桥接，不影响正在用的设备；需要 data/audio 里已有真实录音）。
- 老人每天在用：改完先在测试环境验证，再重跑 `install.sh` 上线；`install.sh` 会先自检再重启服务。
