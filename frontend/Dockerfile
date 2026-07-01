# Frontend image — multi-stage. `dev` target runs Vite; `prod` serves the built
# SPA with nginx and proxies /api, /auth, /health to the backend.

# ---------- dev ----------
FROM node:20-alpine AS dev
WORKDIR /app
COPY package*.json ./
RUN rm -f package-lock.json && npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------- build ----------
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN rm -f package-lock.json && npm install
COPY . .
RUN npm run build

# ---------- prod ----------
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
# nginx config is NOT baked in — it is mounted at runtime from the repo-root
# ./nginx/default.conf by both docker-compose.dev.yml and docker-compose.prod.yml
# (single shared source of truth). See nginx/default.conf.
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
