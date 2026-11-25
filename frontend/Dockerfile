FROM docker.io/library/node:24-alpine AS build
WORKDIR /srv/tiso
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build_ca \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --no-audit --no-fund
COPY . .
ARG VITE_AMAP_JS_KEY
ARG VITE_AMAP_SERVICE_HOST
RUN npm run build

FROM docker.io/library/nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /srv/tiso/dist /usr/share/nginx/html
