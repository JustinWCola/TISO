# 高德 API 验证记录

2026-11-25 · P0 离线验证

计划书指定的候选接口为 `GET https://restapi.amap.com/v3/distance`。验证脚本使用上海 GCJ-02 坐标，发送 `type=1` 和由采样点组成的 `origins`，所有起点共享一个 `destination`。Key 从进程环境读取。

已验证默认 dry run、显式请求预算检查、合成响应的 origin_id 顺序恢复、错误响应与缺项处理。当前没有可用的 Web 服务 Key，也未发起真实请求。

待通过授权账号核验的事项包括 1→1 和 N→1 的真实响应及单位、批量上限、部分失败与限流、与普通路线规划的差异、道路吸附、配额扣减方式、JS Key 配置和数据许可。脚本的 2 次请求上限仅适用于这次验证流程。

[高德距离测量文档](https://lbs.amap.com/api/webservice/guide/api/direction/)

P1 的运行时 Provider 增加了 1 QPS 节流、单请求超时和最多两次有预算的暂时错误重试。离线契约测试使用 HTTP 模拟响应，覆盖反向参数与乱序结果。P0 的真实接口验证仍待执行，Compose 验收使用 Mock。
