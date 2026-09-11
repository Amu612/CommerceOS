# ── build ────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_WS_BASE_URL=ws://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_WS_BASE_URL=$VITE_WS_BASE_URL
RUN npm run build

# ── serve ────────────────────────────────────────────────
FROM nginx:1.27-alpine AS serve
RUN addgroup -S web && adduser -S web -G web
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD wget -qO- http://localhost:8080/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
