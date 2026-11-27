# TISO

TISO 是一个面向中国大陆城市的反向交通可达性分析工具。它使用高德地图和算路服务，展示从周边采样点到目的地的驾车和地铁步行可达性。

## Docker Compose 启动

```sh
docker compose up --build --wait
```

在本机浏览器打开 `http://127.0.0.1:8080`，按页面提示配置高德底图和 Web 服务后即可生成等时圈。Compose 默认只监听回环地址，可在根目录的 `.env` 中设置 `TISO_PORT`。Nginx 提供页面并同源代理 REST 与 SSE，后端端口仅在 Compose 网络中使用。停止服务执行 `docker compose down`。实时任务及采样点存放在进程内存中，历史地图和日程保存在同一 Docker 命名卷内的 `/var/lib/tiso/queries.sqlite3`，服务重启后仍可读取。

在使用自有代理 CA 的构建环境中，通过 BuildKit secret 提供可信 CA 文件。

```sh
TISO_BUILD_CA_FILE=/etc/ssl/certs/ca-certificates.crt \
  docker compose -f compose.yaml -f deploy/compose.build-ca.yaml up --build --wait
```

需要公网部署时，在服务器的 HTTPS 反向代理中接入此回环端口，为站点和 API 配置认证及限速，并对 SSE 关闭缓冲。默认部署适用于本机或经过访问控制的单用户环境。

## 本地开发

后端和前端各用一个终端，命令从项目根目录执行。

```sh
cd backend
uv sync --locked
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm run dev
```

## 运行测试

安装 [uv](https://docs.astral.sh/uv/)，然后运行以下命令。如果云环境中的默认缓存目录不可写，可设置 `UV_CACHE_DIR=/tmp/tiso-uv-cache`。

```sh
cd backend
uv sync --locked
uv run --locked python -m pytest -q
```

## 验证高德接口

在 `backend` 目录运行 `python -m scripts.verify_amap` 即可检查脚本入口，默认离线。仅在获准消耗配额时，通过环境变量 `AMAP_WEB_SERVICE_KEY` 提供高德 Web 服务 Key，再运行以下命令。

```sh
python -m scripts.verify_amap --live --max-requests 2
```

预算 1 仅检查单起点，预算 2 检查单起点与批量起点。脚本不会重试；输出省略 Key 和请求 URL。当前结果记录见 [API 验证](docs/API_VALIDATION.md)。

地铁验证入口为 `uv run --locked python -m scripts.verify_amap_transit`，默认离线。经授权后可加 `--live --max-requests 2`，以 `--origin POI_ID,经度,纬度` 和 `--destination POI_ID,经度,纬度` 提供地铁站。每次请求各传一个 `--departure`，例如 `--departure '2026-12-01T08:00+08:00'`。脚本对同一站点对最多发出 4 次查询，输出脱敏的出发时间、耗时和状态。

## 启用高德

在本机页面点右上角「高德设置」。底图和地点搜索需要高德开放平台的 **Web 端（JS API）Key** 和对应的 **JS API 安全密钥**。配置后会自动加载高德底图，搜索框位于左上浮窗。若 Key 设置了域名白名单，请使它与打开 TISO 的主机名一致，例如 `127.0.0.1` 或 `localhost`。设置面板和内置代理需要通过回环地址访问。[高德安全密钥说明](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)

驾车耗时使用独立的 **Web 服务 Key**。点击左上角「TISO」展开等时圈卡片，设置分析参数后生成等时圈。预算按实际发出的请求计算，含重试。左下方的色带图例显示耗时区间和算路时间。JS 安全密钥和 Web 服务 Key 保存在后端 Docker 命名卷中。已保存的密钥在设置中显示为圆点，可点击眼睛在本机查看原值。清除本地配置后，仍可由环境变量提供默认设置。

菜单可调最长耗时、等时线间隔和最小间距。六边形网格内两圈保留 1 或 2 公里间距，外圈按方向以两倍间距探测。发现可达、接近耗时上限或缺测时，在附近补采。某个方向连续两次探测均比目标多 5 分钟且没有缺测时，跳过一圈后再查，降低漏掉孤立可达区域的概率。最小间距可选 250、500 或 1000 米，默认 250 米，对所有采样点生效。若先触及内部距离或请求预算上限，结果会标记外缘未确认。开启「图层 → 采样点」后，地图会随算路批次显示返回的样本。

可以独立确定的内侧补采和下一轮外圈探测合批发送。加密用实测中点比较前后两版等时线的位置。局部位移不超过 50 米且中点耗时与两端线性估算的差异不超过 45 秒时，可停止该处细化。两项阈值用于决定是否继续采样。

算路请求发出前，用随应用保存的 GCJ‑02 大陆陆地多边形筛掉范围外和海上坐标。海岸线参与闭合，等时面和等时线使用同一多边形裁剪。范围内的点继续合批，范围外的点不消耗高德配额。边界数据用于分析范围判断，沿岸细小地形按数据精度处理。数据来源与转换见 [阶段记录](docs/DECISIONS.md)。

已有环境配置可继续使用：后端读取 `AMAP_WEB_SERVICE_KEY`，网页构建读取 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SERVICE_HOST`。本地开发若未设置 `TISO_SETTINGS_FILE`，页面内保存的配置只在当前后端进程中有效。

## 历史和定时查询

- 分析菜单的「历史结果」可查看和删除自动保存的完整或部分结果。保存内容是用于渲染的等时圈、查询参数和时间元数据。
- 驾车分析页默认立即计算。点击「开启定时」并填写时间，再点击「生成等时圈」，即可按北京时间每天查询。已有日程可在「历史结果」中暂停、恢复或删除。计算由本地后端触发，浏览器可关闭。遇到当前任务占用会等待，恢复服务时每个日程最多补跑一次。启用日程会消耗高德算路配额。
- 裸机开发可设置 `TISO_RESULTS_FILE` 使用持久化 SQLite 文件，未设置时使用内存。

## 地铁 + 步行

在分析卡片选择「地铁」，点击「生成等时圈」。TISO 根据目的地坐标查找本地路网，随附上海、北京、广州、成都、武汉、深圳和杭州的数据。目的地没有本地路网时，TISO 会自动从 OpenStreetMap 获取地铁路线并接着计算等时圈。生成的路网会在重启后保留，Compose 保存在现有设置卷的 `/var/lib/tiso/metro`。本地开发可用 `TISO_METRO_DIR` 指定目录，默认 `backend/.tiso-metro`。

预置路网位于 `backend/app/core/data/metro/`，文件名采用 `城市拼音-OSM关系ID.json.gz`，如 `shanghai-913067.json.gz`。

地铁模式的「步行速度」默认为 1.2 m/s，可在 0.5–7.0 m/s 之间调整。它同时用于目的地附近的直接步行和地铁站外接驳，也可粗略估算自行车或电动车的覆盖范围。站内换乘耗时保留本地路网估算。

站点位置和线路方向来自 © OpenStreetMap 贡献者，路网按 ODbL 1.0 提供。相邻站行车时间按距离和停站开销估算，有合理的线路总耗时时按比例校准。不同线路间的换乘估算为 3 分钟加站内直线步行，同一线路换乘到另一行车方向或班型估算从 2 分钟起。使用本地路网时没有外部地铁算路请求，等时圈反映静态可达性。首末班、实时班次和实际步行道路仍会影响行程。[OpenStreetMap 版权及许可](https://www.openstreetmap.org/copyright)

从 OpenStreetMap 重新生成上海路网，可在 `backend` 目录运行 `uv run --locked python -m scripts.build_metro_network --city 913067`。公共镜像暂时不可用时，可用 `TISO_OVERPASS_URL` 指定 Overpass Interpreter，或使用命令的 `--overpass-json` 参数构建已下载的数据。生成和使用地铁路网无需高德 Web 服务 Key，显示高德底图仍使用 JS API 配置。

## 前端与端到端检查

在 `frontend` 执行 `npm ci`、`npm test` 和 `npm run build`。Compose 启动后，在同一目录执行 `npm run test:e2e`。端到端检查默认使用 `/usr/bin/chromium` 和本机 8080 端口，可通过 `CHROMIUM_PATH`、`TISO_TEST_URL` 覆盖。

当前结果是局部平面近似。GCJ‑02 几何仅用于地图渲染，未知网格单元保持空白。适用范围和已知限制见 [阶段记录](docs/DECISIONS.md)。
