# TISO

TISO 是一个面向中国大陆城市的反向交通可达性分析工具。它使用高德地图和驾车算路服务，展示从周边采样点到目的地的耗时。

## Docker Compose 启动

```sh
docker compose up --build --wait
```

在本机浏览器打开 `http://127.0.0.1:8080`，按页面提示配置高德底图和 Web 服务后即可生成等时圈。Compose 默认只监听回环地址，可在根目录的 `.env` 中设置 `TISO_PORT`。Nginx 提供页面并同源代理 REST 与 SSE，后端端口仅在 Compose 网络中使用。停止服务执行 `docker compose down`。任务结果存放在进程内存中，默认最多保留 8 项，访问时清理超过 30 分钟的终态任务，重启后结果清空。

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

## 启用高德

在本机页面点右上角「高德设置」。底图和地点搜索需要高德开放平台的 **Web 端（JS API）Key** 和对应的 **JS API 安全密钥**。配置后会自动加载高德底图，搜索框位于左上浮窗。若 Key 设置了域名白名单，请使它与打开 TISO 的主机名一致，例如 `127.0.0.1` 或 `localhost`。设置面板和内置代理需要通过回环地址访问。[高德安全密钥说明](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)

驾车耗时使用独立的 **Web 服务 Key**。点击左上角「TISO」展开等时圈卡片，设置分析参数后生成等时圈。预算按实际发出的请求计算，含重试。左下方的色带图例显示耗时区间和算路时间。JS 安全密钥和 Web 服务 Key 保存在后端 Docker 命名卷中。已保存的密钥在设置中显示为圆点，可点击眼睛在本机查看原值。清除本地配置后，仍可由环境变量提供默认设置。

已有环境配置可继续使用：后端读取 `AMAP_WEB_SERVICE_KEY`，网页构建读取 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SERVICE_HOST`。本地开发若未设置 `TISO_SETTINGS_FILE`，页面内保存的配置只在当前后端进程中有效。

## 前端与端到端检查

在 `frontend` 执行 `npm ci`、`npm test` 和 `npm run build`。Compose 启动后，在同一目录执行 `npm run test:e2e`。端到端检查默认使用 `/usr/bin/chromium` 和本机 8080 端口，可通过 `CHROMIUM_PATH`、`TISO_TEST_URL` 覆盖。

当前结果是局部平面近似。GCJ‑02 几何仅用于地图渲染，未知网格单元保持空白。适用范围和已知限制见 [阶段记录](docs/DECISIONS.md)。
