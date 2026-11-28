# 第三方组件与来源致谢

本页是机器可读清单；人话版致敬页见管理台 / 登录页入口 **「致敬」**（`/thanks`），以及 [docs/致谢.md](docs/致谢.md)。

| 组件 | 位置 | 许可 | 来源 |
|---|---|---|---|
| 小智 AI 固件（补丁的基础） | `firmware/`（只含补丁，不含上游代码） | MIT | https://github.com/78/xiaozhi-esp32 |
| Silero VAD 模型 | `models/silero_vad.onnx` | MIT | https://github.com/snakers4/silero-vad |
| StPageFlip（唯一改动：关掉运行时注入 `<style>`，同样的样式原样放在 `page-flip.css`，为了 CSP 不放行内联样式） | `web/vendor/page-flip.browser.js`、`web/vendor/page-flip.css` | MIT | https://github.com/Nodlik/StPageFlip |
| esp-ml307（网络组件，`0002` 补丁修改了 `src/esp/esp_ssl.cc`，改动写在补丁头部） | `firmware/0002-esp-ml307-timeouts.patch`（只含补丁） | Apache-2.0 | https://github.com/78/esp-ml307 |
| shadcn/ui 的 neutral 色板与尺寸变量（只借用了设计变量的取值） | `web/*.css` 里的 `:root` 变量 | MIT | https://github.com/shadcn-ui/ui |

## 思路与硬件说明

- 故事书「每次只问一个往事问题」的思路致敬 [StoryWorth](https://www.storyworth.com/)。
- 管理台首页「一眼可读、没事安静、有事清楚」的排法参考了智能手表表盘的设计思路（只借鉴思路，没有使用任何素材）。
- 我们实际使用并提供 ROM 的板型是征辰 Minicam（`zhengchen-minicam`）；**不是购物推荐**，只说明来源。板上的摄像头不启用，本项目没有拍照功能。

## 运行时依赖

Python 包见 `requirements.txt`，各自遵循其许可：onnxruntime（MIT）、numpy（BSD-3-Clause）、opuslib（BSD-3-Clause，调用系统的 libopus，BSD-3-Clause）、websockets（BSD-3-Clause）、websocket-client（Apache-2.0）、requests（Apache-2.0）、pypinyin（MIT）、jieba（MIT）；Windows 平台条件安装 pyogg（BSD-3-Clause，用于提供 `opus.dll`）。
部署侧常见依赖还包括 FFmpeg（LGPL/GPL，作为外部程序调用，不随仓库分发）、nginx、systemd、certbot、ESP-IDF（Apache-2.0，刷固件时）。

云服务（DeepSeek、阿里云/腾讯云语音识别、MiniMax / CosyVoice）按各家服务条款使用，密钥只放在你自己的服务器上。
