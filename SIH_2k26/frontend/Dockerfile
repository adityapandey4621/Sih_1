# The web app: built once, served by nginx, which also proxies /api to the API container so the browser sees ONE origin
# (cookies stay same-site and no CORS is needed). Build from the frontend/ directory:  docker build -t sih-web frontend
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY api_proxy.inc /etc/nginx/conf.d/api_proxy.inc
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=4s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
