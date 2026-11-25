# TISO

项目计划和阶段验收条件见 docs/PROJECT_PLAN.md，实际进度见 docs/DECISIONS.md。

- 驾车算路方向始终是采样点到目的地。坐标使用 GCJ-02，经度在前。
- 目标点耗时为零。缺测、不可达和接口失败均保留空耗时。
- 在 backend 目录运行 `uv run --locked python -m pytest -q`。自动化测试使用离线模拟数据。
- 高德真实请求需要用户明确授权。通过环境变量提供 Key，禁止将其写入仓库或日志。
