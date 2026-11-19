# 「我在呢」设备固件

设备端用的是开源项目 **小智 AI 聊天机器人 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)**（MIT 许可），我们在它上面打了一个陪聊补丁。本目录只放**补丁和配置示例**，不放完整源码。

| 文件 | 作用 |
| --- | --- |
| `0001-xiaozhi-esp32-companion.patch` | 打在 xiaozhi-esp32 上的陪聊补丁（通用陪聊功能 + 我们基于的 Minicam 板级代码） |
| `0002-esp-ml307-timeouts.patch` | 打在网络组件 78/esp-ml307 上的小补丁（连接/发送超时） |
| `sdkconfig.defaults.companion` | 编译配置示例（默认板型 Minicam；OTA 地址是占位符，要改成你自己的） |

> [!IMPORTANT]
> 补丁基于的上游版本是固定的，请严格使用：
> - xiaozhi-esp32：提交 **`563a4f0a70d34eea5547977f2a39efa401c2ea35`**（2026-09-12，介于 v2.5.0 与 v2.5.2 之间，`CMakeLists.txt` 里版本号为 2.5.0）
> - esp-ml307：提交 **`140852402fca59e17e61bfa1b456555f8a501962`**（3.7.2）
> - ESP-IDF：**v6.0.x**（我们用 v6.0.3 在 Linux 上从干净源码打补丁后，以 Minicam 板型完整编译通过；上游要求 ≥ v6.0.1，不支持 5.x）
>
> **预编译 ROM**：GitHub Release 会附带基于 Minicam 编好的 `merged-binary.bin`（OTA 地址是 `YOUR_DOMAIN` 占位符）。Minicam 用户可直接烧录，再在配网页把 OTA 改成你的服务器；其他板子请自己编译或让 AI 改编。

---

## 一、先想清楚：要不要打补丁？

服务端（本仓库的 `xiaozhi_bridge.py`）说的是小智的标准协议。所以**不打补丁、直接用上游官方固件，只把 OTA 地址改成你自己的服务器，也能用**，只是少一部分功能：

| 功能 | 官方固件 + 改 OTA 地址 | 打了补丁 |
| --- | --- | --- |
| 语音聊天（唤醒词 / 按键开始对话，服务器回语音） | ✅ | ✅ |
| 屏幕显示字幕、表情 | ✅ | ✅ |
| 服务器查电量、调音量等（通过固件自带的 MCP 工具 `self.get_device_status` 等） | ✅（板子本身支持电量检测才有电量） | ✅ |
| 固件远程升级（OTA） | ✅ | ✅ |
| **对讲**：手机通过管理页面和设备双向通话（`system/start_intercom`、`stop_intercom`） | ❌ | ✅ |
| **主动问候**：白天随机 6–7 次主动开口（设备发 `companion/proactive`） | ❌ | ✅ |
| 服务器结束对话（`conversation/end`）、远程重启（`system/reboot`） | ❌ | ✅ |
| 拍一下开始连续对话、再拍一下停（不用唤醒词，不用一直按住） | 取决于板子的按键逻辑（很多板子 BOOT 键就是「开始/结束对话」） | 同左；补丁另外提供 `StartConversation()` / `StopConversation()` 接口，板子按键回调里调用即可 |
| 开机预连、断线静默重连，第一句话不丢 | ❌ | ✅ |
| 自愈：看门狗、屏幕卡死/连接卡死/内存不足/长时间失联自动重启，每天凌晨例行重启 | ❌ | ✅ |
| 断电来电后路由器起得慢，不会掉进配网模式出不来 | ❌（60 秒连不上就进入配网热点） | ✅（有已存 WiFi 就一直重试） |
| 上报上次重启原因、重启前在干什么（服务器端可看崩溃线索） | ❌ | ✅ |
| 默认音量 100%（老人听得清） | ❌（默认 70） | ✅ |

---

## 二、准备环境（装 ESP-IDF v6.0.x）

- **Windows**：用乐鑫的 [ESP-IDF 离线安装器](https://dl.espressif.com/dl/esp-idf/)，选 v6.0.x；装好后用开始菜单里的「ESP-IDF PowerShell / CMD」执行下面的命令。
- **macOS / Linux**：
  ```bash
  git clone -b v6.0.3 --recursive https://github.com/espressif/esp-idf.git ~/esp-idf
  cd ~/esp-idf && ./install.sh esp32s3
  . ~/esp-idf/export.sh      # 每开一个新终端都要执行一次
  ```
  国内网络慢可以先 `export IDF_GITHUB_ASSETS=dl.espressif.cn/github_assets` 再执行 `install.sh`。

验证：`idf.py --version` 能输出 `v6.0.x` 即可。

## 三、拿上游代码并打补丁

在任意目录执行（Windows 下在 ESP-IDF 终端里执行，命令相同）：

```bash
# 1) 上游固件，切到补丁对应的提交
git clone https://github.com/78/xiaozhi-esp32.git
cd xiaozhi-esp32
git checkout 563a4f0a70d34eea5547977f2a39efa401c2ea35

# 2) 打陪聊补丁（把路径换成本仓库 firmware 目录的实际位置）
git apply /path/to/wozaine/firmware/0001-xiaozhi-esp32-companion.patch
#   想保留作者和说明可以用：git am /path/to/.../0001-xiaozhi-esp32-companion.patch

# 3) 网络组件 esp-ml307 放到本地 components 目录并打补丁
#    （补丁让固件改用本地组件 components/esp-ml307，不再从组件仓库下载）
git clone https://github.com/78/esp-ml307.git components/esp-ml307
git -C components/esp-ml307 checkout 140852402fca59e17e61bfa1b456555f8a501962
git -C components/esp-ml307 apply /path/to/wozaine/firmware/0002-esp-ml307-timeouts.patch

# 4) 编译配置
cp /path/to/wozaine/firmware/sdkconfig.defaults.companion .
```

然后**编辑 `sdkconfig.defaults.companion`**，把 `https://YOUR_DOMAIN/xiaozhi/ota/` 换成你自己服务器的地址（服务端部署见仓库根目录 README；路径要以 `/xiaozhi/ota/` 结尾）。

> `git apply` 报错一般是上游提交没切对，先确认 `git log -1` 是 `563a4f0`。

## 四、编译

```bash
# macOS / Linux
export SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.defaults.companion"
# Windows PowerShell 写法：
#   $env:SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.defaults.companion"

idf.py set-target esp32s3
idf.py build
```

- 第一次编译要下载 70 多个组件，耗时 10–30 分钟；产物在 `build/`：应用本体 `build/xiaozhi.bin`（也是 OTA 升级包），资源包 `build/generated_assets.bin`。要一个能直接烧的整包，再执行 `idf.py merge-bin`，生成 `build/merged-binary.bin`（16MB Flash 约 10MB）。
- 改过 `sdkconfig.defaults.*` 后要删掉源码根目录的 `sdkconfig`（或 `idf.py fullclean`）再编译，否则新默认值不生效。
- **出 OTA 升级包**：设备只接受版本号比自己大的固件。编译时可用环境变量指定版本号，例如 `XIAOZHI_PROJECT_VER=2.5.1 idf.py build`。

## 五、烧录

用 USB 数据线把设备接到电脑（大多数 S3 板子直接识别为 USB 串口；识别不到就按住 BOOT 键再插线）。

```bash
idf.py -p PORT flash monitor
```

`PORT` 的写法：Windows 是 `COM3` 之类（设备管理器里看），macOS 是 `/dev/cu.usbmodem*`，Linux 是 `/dev/ttyACM0` 或 `/dev/ttyUSB0`（Linux 没权限时把自己加进 `dialout` 组）。`monitor` 可看日志，按 `Ctrl+]` 退出。

不想装 ESP-IDF 的电脑，也可以只装 esptool（`pip install esptool`）烧别人编好的整包：

```bash
python -m esptool --chip esp32s3 -p PORT -b 460800 write-flash 0x0 merged-binary.bin
# 老版本 esptool（4.x）写法：esptool.py --chip esp32s3 -p PORT -b 460800 write_flash 0x0 merged-binary.bin
```

Windows 上也可以用乐鑫的图形工具 Flash Download Tool，地址填 `0x0`。

## 六、让设备连到你自己的服务器

设备开机先访问 **OTA 地址**，服务器在回复里告诉它 WebSocket 地址、时间等。设置 OTA 地址有三种办法：

1. **编译时写死**（推荐）：就是上面 `sdkconfig.defaults.companion` 里的 `CONFIG_OTA_URL`。
2. **配网页面里改**（官方固件也能用）：设备第一次开机（或长按/菜单进入配网）会开一个名为 `Xiaozhi-XXXX` 的 WiFi 热点。手机连上它，浏览器打开 `http://192.168.4.1`，填好家里 WiFi；在「高级选项 / Advanced」里有 **自定义 OTA 地址（Custom OTA URL）**，填 `https://你的域名/xiaozhi/ota/` 保存即可。这个设置存在设备里（NVS 的 `wifi/ota_url`），优先于编译时的地址。
3. 已经连过服务器的设备，之后可以通过服务端下发新固件（OTA）来改。

验证：设备屏幕不再提示激活码 / 连接失败，服务器日志里能看到设备上报的 `application.version`、`board.type`。

> 主动问候的时段（10:00–21:00）和每日维护重启（凌晨 4 点）按**设备时钟**算，设备时钟由服务器 OTA 回复里的 `server_time.timezone_offset` 决定（默认 480 = 北京时间）。

## 七、换别的板子（我们不推荐某块板，只说明我们基于 Minicam）

补丁里有两块东西：

1. **通用陪聊代码**（`main/application.*`、`crash_info.*`、`wifi_board.cc`、`settings.cc` 等）——对上游支持的 ESP32-S3 WiFi 板子都生效。
2. **Minicam 板级代码**（`main/boards/zhengchen-minicam/`）——因为我们实际用的就是这块板，所以把它放进补丁，并附了预编译 ROM。**这不是购物推荐**，只是「我们基于它」的实话。板上的摄像头**不初始化**，也不注册任何拍照工具。

### 你用的正好是 Minicam

直接烧 Release 里的 `merged-binary.bin`，或按本目录默认 `sdkconfig.defaults.companion` 自己编译即可。烧完后在配网页把 OTA 地址改成你的服务器。

### 你用的是上游已支持的别的板

1. 在 `sdkconfig.defaults.companion` 里把 `CONFIG_BOARD_TYPE_ZHENGCHEN_MINICAM=y` 换成目标板子的 `CONFIG_BOARD_TYPE_xxx=y`（在 `main/Kconfig.projbuild` 里找）。
2. 板子不支持设备端回声消除时去掉 `CONFIG_USE_DEVICE_AEC=y`（对讲时会有回声）。
3. 「拍一下就聊」：在该板按键回调里调用 `Application::GetInstance().StartConversation()` / `StopConversation()`。
4. 任何板子都要做第三节里的 esp-ml307 克隆和打补丁。
5. 本项目不用摄像头。如果你的板子带摄像头，上游板级代码会把它初始化、并注册上游自带的 `self.camera.take_photo` 工具；我们的服务器从不调用它，但想彻底关掉的话，在板级代码里别创建 `Camera`（参照 zhengchen-minicam 的做法）。

### 你用的板上游没有、也不想自己啃 C++ —— 交给 AI

把下面整段复制给你的 AI 编程助手（Claude / Cursor / Codex 等），并附上你板子的引脚说明或厂商小智板级代码：

```text
我在用「我在呢」开源仓（runliu2me/wozaine）的 firmware 补丁。
补丁基于 78/xiaozhi-esp32 提交 563a4f0，默认板型是征辰 Minicam（zhengchen-minicam）。
我实际用的板子是：【在这里写板子型号 / 链接 / 引脚表】
请你：
1) 按 firmware/README.md 把上游 checkout 到 563a4f0 并打上 0001、0002 补丁；
2) 如果上游已有我的板型，只改 sdkconfig.defaults.companion 的 CONFIG_BOARD_TYPE_xxx；
3) 如果上游没有，参照 main/boards/ 里最相近的板（尤其 zhengchen-minicam 或双麦的板），
   为我的板新建板级目录，接好麦/喇叭/屏/按键（不要初始化摄像头，这个项目不拍照），并在按键回调里调用
   Application::GetInstance().StartConversation()/StopConversation()；
4) 编过、能烧再告诉我 PORT 怎么写；OTA URL 用我的 https://域名/xiaozhi/ota/。
不要推荐我去买 Minicam；只帮我把现有板跑起来。
```

ESP32-C3/C6 等内存较小的芯片未验证（补丁里新建了几个 16K 栈的任务），不建议。

## 八、许可与致谢

- 上游 xiaozhi-esp32：MIT License，Copyright (c) 2025 Shenzhen Xinzhi Future Technology Co., Ltd. 及项目贡献者。补丁是它的衍生作品，**同样以 MIT 许可发布，并保留上游版权与许可声明**（见补丁头部及上游仓库 `LICENSE`）。分发打过补丁的源码或固件时，请附上上游的 `LICENSE`。
- esp-ml307：Apache License 2.0，`0002` 补丁修改了其中的 `src/esp/esp_ssl.cc`，补丁头部已注明修改内容。
- 补丁中的 `zhengchen-minicam` 板级代码随陪聊补丁一并按 MIT 提供，便于复现我们实际在用的硬件；**收录它不等于代言或推荐购买该板**。
