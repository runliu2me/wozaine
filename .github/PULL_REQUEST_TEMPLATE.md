## 改动内容

<!-- 简要说明解决了什么问题或新增了什么能力 -->

## 兼容性与注意

<!-- 是否兼容现有 .env / family.json / systemd 部署；如有一次性行为变化请注明 -->

## 验证

- [ ] 全部离线测试通过：`for t in tests/test_*.py; do python3 "$t" || exit 1; done`
- [ ] 静态检查通过：`python3 -m pyflakes *.py tools/ tests/`、`for f in web/*.js; do node --check "$f" || exit 1; done`
- [ ] 脚本检查通过：`bash -n deploy/install.sh && bash -n deploy/docker-entrypoint.sh`
- [ ] 安全门禁通过：`tests/test_web_static.py`、`tests/test_no_camera.py`
- [ ] 未包含任何真实密钥、录音、人名、个人配置（`.env` / `family.json` / `persona.md` / `data/`）
- [ ] （如涉及前端页面）在手机宽度（390px）与深色模式下确认过显示正常
- [ ] （如涉及对话链路）已通过 `tests/run_scenarios.py` 或实体设备验证
