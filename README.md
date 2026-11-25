# TISO

TISO 是一个面向中国大陆城市的反向交通可达性分析工具。当前 P0 原型以目的地为中心，使用离线模拟数据展示从周边采样点出发的驾车耗时。

## Docker Compose 启动

```sh
docker compose up --build --wait
```

在本机浏览器打开 `http://127.0.0.1:8080`，点击「生成等时圈」体验离线 Mock。Compose 默认只监听回环地址；可在根目录的 `.env` 中设置 `TISO_PORT`。Nginx 提供页面并同源代理 REST 与 SSE，后端端口仅在 Compose 网络中使用。停止服务执行 `docker compose down`。任务结果存放在进程内存中，默认最多保留 8 项，访问时清理超过 30 分钟的终态任务，重启后结果清空。

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

## 运行离线 CLI

需要 Python 3.11 或更新版本。CLI 只依赖 Python 标准库。

```sh
cd backend
python -m app.cli --lon 121.4737 --lat 31.2304
```

JSON 使用 GCJ-02，经度在前，包含目标点、耗时与距离、采集时间和模拟标识。示例中心耗时为零，北侧样本故意模拟不可达，耗时为 `null`。东西两侧的耗时不同，用于展示方向性。

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

## 启用高德

有明确的调用授权后，可在根目录的 `.env` 中设置 `AMAP_WEB_SERVICE_KEY`，再运行 `docker compose up --wait`。网页继续默认使用 Mock，通过数据源选项选择高德后才会使用真实算路。预算按实际发出的请求计算，含重试。Web 服务 Key 只交给后端容器。

底图与地点搜索需要单独的 JS API Key 和已配置的高德 JS 安全代理。将公开的 `VITE_AMAP_JS_KEY` 和带 `/_AMapService` 路径的 `VITE_AMAP_SERVICE_HOST` 写入 `.env`，执行 `docker compose up --build --wait`。JS 安全密钥应保留在该代理。页面默认使用离线坐标视图，可以主动切换高德底图。底图和搜索的真实运行仍需按账号权限验证。

## 前端与端到端检查

在 `frontend` 执行 `npm ci`、`npm test` 和 `npm run build`。Compose 启动后，在同一目录执行 `npm run test:e2e`。端到端检查默认使用 `/usr/bin/chromium` 和本机 8080 端口，可通过 `CHROMIUM_PATH`、`TISO_TEST_URL` 覆盖。

当前结果是局部平面近似。GCJ‑02 几何仅用于地图渲染，未知网格单元保持空白。适用范围和已知限制见 [阶段记录](docs/DECISIONS.md)。
