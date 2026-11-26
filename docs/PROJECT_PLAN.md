# TISO 项目计划书

版本：v1.0（开发基线） | 项目性质：个人、非商业用途的 Web GIS 工具 | 日期：2026-11-25

## 1. 项目背景与目标

TISO 是面向中国大陆城市的交通可达性分析工具。用户在地图上选择目的地，设置交通方式、最大通行时间、采样精度等参数，系统通过地图服务商算路接口构建从周边位置到目的地的通行时间场，并绘制等时线与分级填色区域。典型用途包括租房通勤、机场/车站住宿选择、旅行规划和多地点可达性比较。

核心定义是反向等时圈，而不是从目的地向外出发的正向等时圈。所有驾车请求的 origins 为采样点，destination 为目标点，不得交换方向。第一版只计算查询期间当前路况下的预计耗时，不声称支持未来路况预测。

### 1.1 范围与里程碑

| 阶段 | 功能 | 完成条件 |
|---|---|---|
| P0 | API 验证、Mock、CLI 原型 | 真实接口验证记录或明确阻塞；Mock 可离线运行 |
| P1 / MVP | 实时驾车、均匀采样、等时圈、地图交互 | 浏览器端完整跑通，含失败处理和预算控制 |
| P2 | 自适应加密、渐进预览、导出、查询记录 | 与均匀网格基准进行误差和成本对比 |
| P3 | 公交/地铁、指定到达时间 | 验证时间参数语义，支持近似反向搜索 |
| P4 | 多目的地、对比、其他交通方式与 Provider | 通过统一能力接口扩展 |

不属于 MVP：未来驾车路况预测、历史交通采集、账户系统、支付、微服务、独立路网引擎、机器学习交通模型、TREK 集成、自动选址推荐。不得因后续功能阻塞 MVP。

## 2. 产品需求

### 2.1 主界面

全屏地图为主体，左侧可折叠控制面板，右下角图例。桌面优先，移动端可使用底部抽屉。

- 目的地：关键词搜索、地图单击、直接输入 GCJ-02 经纬度；显示地址与 marker，可拖动修改；未点击生成前不触发算路。
- 交通方式：MVP 只启用驾车；公交、骑行、步行可隐藏或标注尚未实现，不得提供假功能。
- 计算模式：当前路况；展示采集开始/结束时间、时区、数据来源和近似结果声明。
- 参数：最大通行时间默认 60 分钟，可选 15–120 分钟；等时线间隔默认 15 分钟，可选 5/10/15/20/30 分钟；半径默认 15 km，可调；快速/标准/精细分别建议 2 km/1 km/500 m 初始间距；高级配置允许调整请求预算。
- 生成前显示预计采样点数、批次数及预算上限；参数无效或超预算时禁止提交并提示解决方法。
- 生成任务：显示采样、算路、插值、几何处理、完成等阶段，显示已完成点数、失败点数、请求次数；可取消。修改参数后旧图层应明显标为旧结果，不能混淆。
- 结果：连续色带或离散分级填色；可切换仅等时线、等时面、采样点；点击结果展示近似耗时及是否实测；未知区域不填色。实时路况底图开关可选。
- 地图图例标记时间单位、方向（到达目的地）、数据采集时间窗口；仅在有效区域绘制，不夸大精度。
- P2 增加 GeoJSON 导出、配置导出及合规历史记录；默认不长期保存第三方原始算路数据，待许可确认后再开启。

## 3. 技术选型与架构

模块化单体，前后端分离，单进程后端起步。

| 层级 | 技术 | 说明 |
|---|---|---|
| 前端 | React + TypeScript + Vite | SPA |
| UI | Tailwind CSS + shadcn/ui | 轻量组件；不要求大量组件库 |
| 状态 | Zustand 或 React 本地状态 | 优先简单方案，服务端状态单独管理 |
| 地图 | 高德 JS API 2.0 | 独立 MapAdapter；无需同时引入 Leaflet |
| 后端 | Python 3.11+ / FastAPI / Pydantic | REST + SSE |
| HTTP | httpx.AsyncClient + asyncio | 连接复用、限流、超时、取消 |
| 计算 | NumPy / SciPy / ContourPy | 时间场、插值、等值线 |
| GIS | PyProj / Shapely | 米制坐标和拓扑处理 |
| 存储 | MVP 内存；P2 可选 SQLite | 单用户任务；默认不持久化敏感数据 |
| 测试 | pytest / Vitest / Playwright | 单测、契约、E2E |
| 部署 | Docker Compose | 前端静态服务反代后端；本地开发独立运行 |

业务流：UI → REST 创建任务 → TaskManager → SamplingEngine → RoutingProvider → TimeField → ContourEngine → GeoJSON → UI 地图图层。SSE 负责进度与状态，不承载大体积结果；前端完成后通过 REST 拉取结果。

### 3.1 目录结构

```text
TISO/
├── AGENTS.md
├── README.md
├── .env.example
├── .gitignore
├── compose.yaml
├── docs/
│   ├── PROJECT_PLAN.md
│   ├── API_VALIDATION.md
│   ├── ARCHITECTURE.md
│   └── DECISIONS.md
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── features/map/
│   │   ├── features/isochrone/
│   │   ├── stores/
│   │   └── types/
│   └── tests/
└── backend/
    ├── pyproject.toml
    ├── app/
    │   ├── main.py
    │   ├── config.py
    │   ├── api/
    │   ├── schemas/
    │   ├── providers/
    │   │   ├── base.py
    │   │   ├── amap.py
    │   │   └── mock.py
    │   ├── core/
    │   │   ├── sampling.py
    │   │   ├── coordinates.py
    │   │   ├── time_field.py
    │   │   ├── contours.py
    │   │   └── engine.py
    │   ├── tasks/
    │   └── storage/
    ├── scripts/verify_amap.py
    └── tests/
```

模块边界：providers 不依赖前端和等值线算法；core 不直接发 HTTP；tasks 管理生命周期与资源；api 只做参数校验、调用和序列化；MapAdapter 封装 SDK 创建、销毁、事件与图层操作。

## 4. 地图接口与验证（P0 必须完成）

### 4.1 高德驾车距离测量

候选接口：GET https://restapi.amap.com/v3/distance
参数：key、origins（经度,纬度，使用 | 分隔）、destination、type=1。预期结果 results 包含 origin_id、dest_id、distance、duration（秒）。根据 origin_id 恢复输入顺序，不能假设返回顺序与输入一致。HTTP 200 也可能是业务失败，检查 status、info、infocode 与单条结果。

官方文档：https://lbs.amap.com/api/webservice/guide/api/direction/
计费页面：https://lbs.amap.com/pages/base_service_price
JS API：https://lbs.amap.com/api/javascript-api-v2/summary

前期讨论中的 100 点/批、3 QPS、15 万次/月均为待核验配置，不能当成固定授权或计费承诺。默认使用保守配置（20 点/批、1 QPS、1 个在途请求），验证后按账号权益调整；最大批大小还需考虑 URL 长度。不得绕过配额、轮换账号或无限重试。

### 4.2 验证脚本

使用用户配置的 Key，默认不自动发真实请求。显式传入 --live 和 --max-requests 后才允许联网。默认 Mock 无费用。

1. 用上海或用户指定测试地点验证 1→1 及 N→1 请求。
2. 验证返回字段、单位、坐标系、请求最大起点数量、部分失败、错误码、限流行为。
3. 比较少量同起终点的距离测量与普通驾车规划耗时，记录差异和路况语义；不得假设距离测量等于最快路线。
4. 验证道路吸附、跨河、单行道及不可达位置。无法获得吸附点时记录局限。
5. 由用户在控制台核验调用前后配额扣减，确定按批还是按 OD 对计量；脚本不应声称能自动读取未授权控制台。
6. 验证前端 JS Key 与后端 Web Service Key 的类型；检查 JS 安全密钥代理配置及数据许可。

记录到 docs/API_VALIDATION.md：日期、接口文档、脱敏请求、实际结果、未验证项、建议配置。没有真实 Key 或网络时仍可完成 Mock 主流程，但必须明确未验证，不得编造结果。

### 4.3 Provider 契约

```python
@dataclass(frozen=True)
class Capabilities:
    modes: frozenset[str]
    supports_matrix: bool
    supports_departure_time: bool
    supports_arrival_time: bool
    max_origins: int
    coordinate_system: str

class RoutingProvider(Protocol):
    def capabilities(self) -> Capabilities: ...
    async def travel_time_matrix(self, origins, destination, mode,
                                 departure_time=None) -> list[TravelResult]: ...
```

TravelResult 应包含 point_id、duration_s（可空）、distance_m（可空）、status（ok/unreachable/error）、provider_error、observed_at。不可达与接口失败不能混为一谈。当前驾车 Provider 必须拒绝非空 departure_time，避免静默忽略时间参数。Mock 使用确定性人工时间场，可模拟障碍、方向性、缺测、限流和失败；明显标记模拟数据。

## 5. 空间算法

### 5.1 坐标体系

API 与高德前端坐标使用 GCJ-02，经度在前。内部需要米制局部坐标。MVP 可明确采用以目的地为原点的局部近似平面，在限定范围内对 GCJ-02 数值进行局部尺度换算：x = R cos(lat0) Δlon，y = R Δlat（角度用弧度），逆变换返回 GCJ-02；使用合适地球半径并测试误差。不要把 GCJ-02 直接标为 EPSG:4326 或假装能通过标准投影去除偏移。若采用 PyProj，需要先明确合法、可验证的坐标转换链。内部 GeoJSON 形状若使用 GCJ-02，必须标记为地图渲染专用、非 RFC 7946 标准地理坐标；标准 GeoJSON 导出需可靠转换为 WGS84，否则仅导出带坐标系元信息的 JSON。

### 5.2 MVP 均匀网格

1. 根据半径 R 生成目标周边规则方格节点；网格步长 h，以目的地为格点原点。
2. 保留圆形范围及必要边界缓冲点，便于插值；明确显示有效范围，范围外不外推。以实际生成节点数估算预算，不能只使用面积公式。
3. 目标点耗时定义为 0（不发算路请求）；其他点与目标点构成反向 OD。
4. 为每点分配稳定整数网格索引与 ID；去重后分批查询；记录每批采集时刻。
5. 对跨河、封闭园区、道路远离的点，MVP 标注未知风险；不能把算路成功等同于原始地面位置可通车。缺失点不得以零或任意大常数代替。
6. 时间场按秒存储，显示转换为分钟。计算默认最大 60 min，15 min 分级；不提前截断大耗时，保留用于边界插值的有效数值。
7. 若外圈仍存在目标阈值内点，标记范围截断，并提示扩大半径；MVP 不自动无限扩展。

### 5.3 插值与等值线

规则网格使用双线性插值或直接在规则网格上使用 ContourPy；优先直接生成等值线和分级填色多边形，不必创建过密栅格。缺测节点使用掩膜，默认禁止跨未知单元插值（例如 corner_mask=False）。不使用高阶无约束插值，避免负耗时和不合理超调。

对于阈值 L，理论区域 A(L)={p | D(p)≤L}；显示阈值之间的不重叠色带。等值线集合应支持 MultiLineString；等时面应支持 MultiPolygon 与内孔。ContourPy 的 filled 输出需按外环/内环分组构建几何，不得把所有环当独立多边形；使用 Shapely 检查有效性并记录修复。边缘与分析域裁剪，避免跨未知区域填充。简化容差以米计且保留拓扑，默认关闭或采用保守值。测试嵌套性、无重叠、洞、多连通区域、鞍点、边界截断及全缺测。

结果中的采样点数、成功率、采集窗口、采样间距、边界截断和未知区比例必须可见。空间插值精度不能等同于真实交通精度；跨河等障碍在无掩膜数据时可能产生错误连通，UI 应提示。

### 5.4 P2 自适应六边形采样

六边形格心是采样节点。初次算路先查内两圈，最长耗时不超过 30 分钟时用 1 km 间距，其余用 2 km。外圈按六边形的六个方向以两倍间距探测，相邻方向共享边界样本，多个方向合批。命中「目标耗时 + 5 分钟」内或发现缺测时，补齐内侧相邻粗格，边界命中时也探测相邻方向。连续两次探测全部有效且超过该阈值时，跳过一圈后复查。所有方向均复查通过才确认外缘。在仍有活跃方向时，已确认的方向每隔三圈再次探测。采样受内部 60 km 范围以及点数与请求总量限制，外缘尚未确认时标记部分结果。自动范围按已采样三角网裁剪，无支撑区域留白。历史调用方仍可显式指定半径。

以粗格心建立三角剖分，优先补采穿过等时线的三角形边中点，再处理有效/未知边界、高梯度和内部验证误差。能独立确定的内侧补采与下一轮外圈探测合批，仍参与本轮方向决策的点先算。任务内共享节点表去重。

实测中点将三角形分成四块，比较各时间阈值的加密前后线段。局部双向线距不超过 50 米，且中点实测耗时与两端线性估算相差均不超过 45 秒时，可暂停该处等时线加密。若等时线出现或消失、共享边后续发现较大位移或边中点缺测，则保留继续加密的机会。此前位置不稳定的三角形至少再确认一次。最小间距可选 250、500 或 1000 米，默认 250 米，并与点数、请求和最多 4 轮的限制共同生效。最终结果使用共享边节点约束的分片线性插值，缺测区域保持空白。

### 5.5 P3 公共交通与到达时间

候选接口：https://restapi.amap.com/v5/direction/transit/integrated；验证 date/time、城市编码、换乘策略、夜班车及 duration 语义（含等车时间的官方描述仍需实际测试）。不能假设它有批量功能或真正按未来时刻预测所有公交路况。

设目标到达 T、候选出发 t、耗时 D(p,t)，求最晚可行出发 t*=max{t:t+D(p,t)≤T}，时间场为 T-t*。先按固定阈值 L 查询 t=T-L，测试 D≤L；在最早到达函数具备 FIFO/允许合理等待等条件时，可用于嵌套反向等时圈。否则采用离散候选时刻搜索并注明近似误差，不直接假设二分单调。跨日、时区、首末班车、换乘与无路线需专项测试。采用更严格预算和粗采样/自适应策略。

## 6. 后端 REST / SSE 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/v1/health | 健康检查，不暴露 Key |
| GET | /api/v1/capabilities | Provider、交通方式、时间能力、有效参数 |
| POST | /api/v1/isochrones/estimate | 本地估算点数、批次及预算，不调用外部 API |
| POST | /api/v1/isochrones/jobs | 创建任务，返回 202 与 job_id |
| GET | /api/v1/isochrones/jobs/{id} | 状态及进度快照 |
| GET | /api/v1/isochrones/jobs/{id}/events | SSE 进度事件，支持重连快照 |
| GET | /api/v1/isochrones/jobs/{id}/samples?after=0 | 增量读取已返回采样点，每页最多 256 个，使用返回的 next 继续读取 |
| GET | /api/v1/isochrones/jobs/{id}/result | 完成后的结果 |
| POST | /api/v1/isochrones/jobs/{id}/cancel | 取消任务，幂等 |

请求示例：
```json
{"destination":{"lng":121.5,"lat":31.24,"crs":"GCJ-02"},"max_duration_min":60,"contour_interval_min":15,"min_spacing_m":250}
```
字段范围在服务端校验，客户端配置不得突破服务器硬上限。估算与执行共用采样逻辑。返回元信息包括 job_id、provider、mode、coordinate_system、traffic_mode、sample_count、success_count、unreachable_count、error_count、request_count、started_at、finished_at、coverage、warnings；几何字段包含 contours、bands，均为 FeatureCollection 形状。MVP GCJ-02 几何仅作地图渲染数据，不宣称为标准 GeoJSON。

SSE 事件：snapshot、progress、warning、completed、failed、cancelled、heartbeat。每次事件含递增 sequence、job_id、stage、completed_points、total_points、requests_sent；重连时先发当前快照，终态后关闭。进度可以分阶段，不伪造百分比。异常统一返回结构化错误 code/message/details，不返回供应商原始凭证或完整带 Key URL。

## 7. 任务执行、资源与安全

- 状态机：queued → running → completed/partial/failed/cancelled；状态互斥，终态不可逆。partial 必须显式说明缺测和原因。
- 单用户 MVP 默认同时仅一个计算任务；使用全局限速器（token bucket 或严格时间间隔）和并发 semaphore，不能只依赖并发数实现 QPS。
- 预算在任务创建及每一次实际请求前检查，重试也计入；同时统计 HTTP 请求和 OD 对数。默认 max_points=2000、max_requests=150，可在配置中调整；网格预计超限则拒绝或由用户主动降低精度，不静默改参。
- 每批超时建议 15s；网络错误/429/临时服务端错误有限重试（最多2次、指数退避与抖动、遵守 Retry-After）；认证、参数、余额/配额错误立即停止或按明确策略处理。限流与重试行为可测试。
- 取消后停止派发新批次，取消在途等待并忽略迟到结果；已被供应商接收的请求无法保证撤回或退款。
- 将 NumPy/SciPy 几何计算放入线程执行，避免阻塞异步事件循环；单进程 MVP 不使用多 worker 共享内存任务状态。任务结果保留时间与上限可配置，重启丢失明确告知。
- .env 仅本地；后端 Key 不得出现在前端 bundle、日志、SSE、URL 回显、Git 或测试快照。前端 JS Key 按平台域名/安全配置使用；如需安全代理由后端提供，禁止任意 URL 转发。CORS 开发环境限制具体 origin；生产同源反向代理；默认绑定本地地址，公网部署前增加认证和速率限制。
- 默认不进行后台定时采集、不绕过平台配额、不记录用户精确地址到公共日志。任何长期缓存、离线导出、第三方路网混合都需核查服务条款。只允许任务内去重与明确许可范围内的短期缓存。

建议配置：AMAP_WEB_KEY、VITE_AMAP_JS_KEY、AMAP_JS_SECURITY_CODE（若采用服务端安全代理）、ROUTING_PROVIDER=mock、AMAP_BATCH_SIZE=20、AMAP_QPS=1、AMAP_MAX_CONCURRENCY=1、JOB_MAX_POINTS=2000、JOB_MAX_REQUESTS=150、JOB_TTL_SECONDS=1800。具体 JS 安全代理字段以官方文档为准。前端不读取后端凭证。

## 8. 测试与验收

### 8.1 P0/MVP 验收清单

- 无 Key 时可使用 Mock 完整生成地图，且有显著模拟标识；没有 JS Key 时提供本地示意画布/坐标视图或明确的地图配置提示，不能让算法测试依赖外部 SDK。
- 有有效 Key 且用户明确触发时，按 N→1 查询真实耗时；记录实际调用情况，验证 origin_id 映射及失败区分。
- 用户能选择目的地、调整范围/精度/等时线、看到估算、生成、取消、查看图例和结果；重复生成不叠加泄漏图层。
- 任务错误、超时、超预算、部分结果均能得到合理反馈；任何异常不得将缺失数据绘为 0 分钟可达。
- 所有地图结果方向正确，输入输出坐标顺序正确，中心点为 0，未知与范围外区域不绘制；显示路况采集时间及近似性质。
- 自动化测试不发送真实付费请求，真实 smoke test 为显式 opt-in 且限额。
- 后端 pytest、前端类型检查/测试/构建、至少一个 Mock E2E 通过；README 可从干净环境复现运行。

### 8.2 测试矩阵

| 类型 | 覆盖内容 |
|---|---|
| 单元 | 坐标往返、网格去重、计数、分批、单位、插值、等值线几何 |
| 合成时间场 | 径向线性场、方向性场、快速通道、孔洞、断裂、全缺测、鞍点 |
| Provider 契约 | 成功、顺序打乱、空结果、部分失败、业务错误、限流、超时 |
| 任务 | 取消、重复取消、预算上限、重试计量、状态转移、SSE 重连 |
| 前端 | 参数校验、图层销毁、图例、进度、错误提示、模拟标识 |
| 集成 | Mock→FastAPI→SSE→React→地图结果；可选真实接口 smoke |

对解析可验证的合成线性时间场，在有效网格内检查插值误差和阈值边界位置；容差与网格间距相关，不能设定无依据的真实交通误差保证。P2 用独立验证点计算 MAE、P90 绝对误差、等时面 IoU、点数、HTTP 次数、OD 数量、耗时；高密度结果本身仍是近似基准。真实路况对比需缩短采集间隔以降低时间变化干扰。

## 9. 开发执行顺序

P0：建立仓库、环境配置、Provider 契约、Mock、验证脚本、最小 CLI；输出验证报告。CLI 能生成带坐标系说明的结果 JSON。无真实 Key 时继续 Mock 开发，不编造验证成功。

P1a：实现规则网格、时间场、掩膜、等时线/面、预算估算和算法单测；使用人工数据做可视化验证。

P1b：实现 FastAPI、任务管理、全局限流、取消、SSE、结构化错误及接口测试。

P1c：实现 React UI、高德 MapAdapter、图例、参数控制、估算、实时状态和结果图层；Mock 模式可脱离真实算路 Key。

P1d：完成 Docker Compose、配置文档、端到端测试、真实 API 可选验证、已知限制和发布检查。形成可运行 MVP 后再开始 P2/P3，不同时铺开所有功能。

## 10. 给 Codex 的执行规则

1. 先检查现有仓库、git 状态、依赖和 AGENTS.md，不覆盖用户已有实现或未提交工作。若空仓库则初始化结构。
2. 将本计划复制到 docs/PROJECT_PLAN.md，创建简洁 AGENTS.md 和任务清单，记录实际选型与偏差。优先使用现有可维护工具，不随意引入大依赖。
3. 每个阶段实现真实可运行代码和测试，不只输出计划、伪代码或空壳。先 P0，再 P1a、P1b、P1c、P1d。
4. 所有外部接口字段以官方文档与真实验证为准；文档不确定的数值做可配置参数，明确待验证。禁止伪造 API 响应作为真实结果。
5. 无 Key/网络时不阻塞 Mock 开发。不得擅自注册服务、购买配额、发大量请求、提交/推送远端或公开凭证。真实请求需用户明确授权。
6. 每阶段运行相关测试与构建，记录命令和真实结果；失败时定位原因，不能删除测试或吞掉异常来制造通过。保留 README 中的本地启动、配置、Mock、真实 smoke test 与部署说明。
7. 维护 docs/DECISIONS.md，注明假设、实现差异、风险、未验证事项。若需改变核心架构或新增付费服务，先说明原因，不自行扩大范围。
8. 最终交付：完整源代码、锁定依赖、README、.env.example、Docker Compose、测试、API 验证记录、已知限制，以及实际运行/测试总结。

## 11. 参考资料与待核验事项

- 高德距离测量与路径规划：https://lbs.amap.com/api/webservice/guide/api/direction/
- 高德路径规划 2.0：https://lbs.amap.com/api/webservice/guide/api/newroute
- 高德基础服务计费：https://lbs.amap.com/pages/base_service_price
- 高德 JS API 2.0：https://lbs.amap.com/api/javascript-api-v2/summary
- ContourPy：https://contourpy.readthedocs.io/
- FastAPI：https://fastapi.tiangolo.com/
- Shapely：https://shapely.readthedocs.io/

本计划基于此前技术讨论编制，未实际调用高德账号，也未重新核实所有官方文档的最新版本。关键待验证事项包括批量上限、扣费单位、个人权限、当前路况语义、路网吸附、公交时间参数、平台数据许可。计划中的 UI 布局、默认采样间距、范围、限速及预算均为建议初始值，而非平台承诺或用户明确指定值。
