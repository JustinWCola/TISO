<p align="center">
  <img src="./assets/tiso-logo.svg" width="96" height="96" alt="TISO 时钟图标">
</p>

<h1 align="center">TISO</h1>

<p align="center"><strong>在地图上看清，多久能到达目的地。</strong></p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#能做什么">能做什么</a> ·
  <a href="#本地开发">本地开发</a> ·
  <a href="#数据与限制">数据与限制</a>
</p>

TISO 是面向中国大陆城市的**反向交通可达性分析工具**。在高德地图上点选或搜索目的地，就能查看从周边各处出发，驾车或乘地铁加步行，在指定时间内能到达目的地的范围。

## 快速开始

在装有 Docker Compose 的机器上运行以下命令。

```sh
git clone https://github.com/JustinWCola/TISO.git
cd TISO
docker compose up --build --wait
```

1. 打开 [本地 TISO](http://127.0.0.1:8080/)，点击右上角的钥匙图标。
2. 填写高德 **Web 端 JS API Key** 和对应的 **JS API 安全密钥**。使用驾车分析时，再填写独立的 **Web 服务 Key**。如启用了域名白名单，请放行实际访问的主机名，例如 `127.0.0.1`。[高德安全密钥说明](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)
3. 在地图上点选目的地，或展开左上角菜单搜索地点。选择「驾车」或「地铁」，设置时长后点击「生成等时圈」。

Compose 默认只监听本机回环地址。可在项目根目录的 `.env` 中设置 `TISO_PORT`，停止服务时执行 `docker compose down`。历史结果、定时日程、高德设置和已生成的地铁路网保存在 Docker 命名卷中，服务重启后仍可使用。

## 能做什么

| 功能 | 用法 |
| --- | --- |
| **驾车等时圈** | 用高德当前路况计算到达目的地的时间。设定最长耗时、等时线间隔和最小采样间距。 |
| **地铁 + 步行等时圈** | 用本地地铁路网估算乘车、换乘和接驳步行时间，可调整步行速度。 |
| **地图选点与地点识别** | 点击地图或拖动图钉后识别附近的 POI，算路使用实际选中的坐标。 |
| **查看采样过程** | 点击右下角图层图标开启「采样点」，驾车算路时会逐批显示结果。左下角图例显示耗时区间和算路时间。 |
| **历史与定时查询** | 「历史结果」可查看或删除保存的结果。驾车模式下开启定时，按北京时间每天运行，日程可暂停、恢复或删除。 |

定时查询由本地后端执行，关闭浏览器后仍会运行。定时卡片置顶显示在历史结果中。若计算服务重启，每个日程最多补跑一次。

## 本地开发

从项目根目录打开两个终端，分别启动后端和前端。后端使用 [uv](https://docs.astral.sh/uv/)，前端使用 Node.js 和 npm。

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

### 测试

从项目根目录分别执行以下命令。

```sh
cd backend
uv run --locked python -m pytest -q
```

```sh
cd frontend
npm ci
npm test
npm run build
npm run test:e2e
```

端到端测试需要先启动 TISO。测试使用模拟数据，默认连接本机 8080 端口并使用 `/usr/bin/chromium`，可通过 `TISO_TEST_URL` 和 `CHROMIUM_PATH` 覆盖。

<details>
<summary>环境变量与部署细节</summary>

- [.env.example](./.env.example) 提供 Compose 可用的变量模板。后端可从 `AMAP_WEB_SERVICE_KEY` 读默认 Web 服务 Key，前端构建可用 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SERVICE_HOST`。
- 裸机后端可设置 `TISO_SETTINGS_FILE` 和 `TISO_RESULTS_FILE` 持久化高德设置与历史结果。省略时，数据保留在当前后端进程中。Compose 使用 `/var/lib/tiso` 下的配置文件、SQLite 数据库和地铁路网目录。
- Nginx 将 REST 和 SSE 同源代理到后端。对外部署时，由 HTTPS 反向代理接入回环端口，为站点和 API 配置认证及限速，并关闭 SSE 缓冲。高德设置面板和内置代理仍通过回环地址访问。
- 在使用自有代理 CA 的环境中，可通过 BuildKit secret 提供可信 CA 文件。

```sh
TISO_BUILD_CA_FILE=/etc/ssl/certs/ca-certificates.crt \
  docker compose -f compose.yaml -f deploy/compose.build-ca.yaml up --build --wait
```

</details>

<details>
<summary>高德接口验证</summary>

在 `backend` 目录运行以下脚本可检查入口，默认使用离线模式。

```sh
uv run --locked python -m scripts.verify_amap
uv run --locked python -m scripts.verify_amap_transit
```

真实验证需先获得配额授权，通过环境变量提供 `AMAP_WEB_SERVICE_KEY`，再加 `--live --max-requests 2`。驾车验证的两次请求分别覆盖单起点和批量起点。地铁验证还需要用 `--origin` 和 `--destination` 指定站点，格式为 `POI_ID,经度,纬度`，并用 `--departure` 指定出发时间，例如 `2026-12-01T08:00+08:00`。脚本输出省略密钥和请求 URL。

</details>

## 数据与限制

- **驾车** · 使用 GCJ‑02 坐标，算路方向为**采样点到目的地**。自适应六边形网格在内圈采样、沿外圈分方向探测，再对可达边缘加密。海上和大陆陆地范围外的采样点在请求前筛除，等时线和等时面按海岸线裁剪。缺测区留白，距离或配额先触及上限时会标记外缘未确认。
- **地铁** · 随附上海、北京、广州、成都、武汉、深圳和杭州的路网。首次分析其他城市时会尝试从 OpenStreetMap 构建路网并保存在本地。结果采用静态估算，实际班次、首末班和步行道路会影响行程。[OpenStreetMap 版权及许可](https://www.openstreetmap.org/copyright)

<details>
<summary>采样参数和地铁路网</summary>

- 驾车网格的最小间距可选 250、500 或 1000 米，默认 250 米。内圈以 1 或 2 公里开始采样，外圈以两倍间距探测。发现可达、接近上限或缺测时补采。等时线加密同时考虑实测中点耗时误差和等时线位移，当前阈值分别为 45 秒和 50 米。
- 地铁步行速度默认 1.2 m/s，可在 0.5–7.0 m/s 之间调节，用于直接步行与站外接驳。站间行车时间按距离和停站开销估算，换乘时间由本地路网估算。预置路网位于 [backend/app/core/data/metro/](./backend/app/core/data/metro/)。
- 在 `backend` 目录运行 `uv run --locked python -m scripts.build_metro_network --city 913067` 可从 OpenStreetMap 重建上海路网。可用 `TISO_OVERPASS_URL` 更换 Overpass 服务，或通过 `--overpass-json` 使用已下载的数据。本地开发可用 `TISO_METRO_DIR` 指定保存目录。

</details>
