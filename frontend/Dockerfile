FROM node:24.14.0-bookworm-slim AS build
WORKDIR /site
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_PHYLO_PUBLIC_API_BASE_URL=http://localhost:8200
ENV VITE_PHYLO_PUBLIC_API_BASE_URL=$VITE_PHYLO_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:24.14.0-bookworm-slim
WORKDIR /site
ENV NODE_ENV=production
COPY --from=build /site /site
EXPOSE 3200
CMD ["./node_modules/.bin/vinext", "start", "--host", "0.0.0.0", "--port", "3200"]
