# 参与贡献

欢迎！尤其欢迎这些：

- 你家老人用下来发现的「不像」「不体贴」的地方，以及你在人设里怎么改好的（去掉隐私后分享）。
- 更多小智板子的适配经验、外壳设计。
- 新的语音识别 / 合成 / 大模型服务商接入（接口见 [docs/定制指南.md](docs/定制指南.md) 第 9 节）。
- 文档里写得不清楚的地方。

## 提交代码之前

```bash
for t in tests/test_*.py; do python3 "$t" || break; done   # 离线、免费，CI 也跑这些
for f in web/*.js; do node --check "$f"; done
python3 -m pyflakes *.py tools/ tests/
bash -n deploy/install.sh
```

改了管理台的，用 `tools/demo_data.py` + `tools/demo_bridge.py` 在手机宽度（390px）和深色模式下各看一遍；页面里不要写内联 `style=`、`<script>` 或 `onclick`（CSP 会拦，`tests/test_web_static.py` 也会报错）。

改了对话链路的，再跑一遍 `tests/run_scenarios.py`（会真实调用云服务，花几分钱）。

## 三条底线

1. **永远不要提交任何真实的对话、录音、人名、密钥。** 测试和示例里一律用虚构的人（王秀英、小明、张伟……）。
2. **老人那边永远不能「没反应」。** 任何新功能出错时，都要有兜底的话术或降级方案，并且能告警。
3. **不加拍照、录像、定位这类功能。** 这个项目靠声音陪伴，不做监视；`tests/test_no_camera.py` 会拦住拍照相关代码。
