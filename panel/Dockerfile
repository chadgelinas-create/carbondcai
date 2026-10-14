# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=24.18.0

FROM node:${NODE_VERSION}-alpine AS dependencies
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
RUN chown -R node:node /app

FROM dependencies AS development
COPY --chown=node:node . .
USER node
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM development AS quality
CMD ["npm", "run", "check"]

FROM development AS build
ARG VITE_SKID2_SIMULATION_ENABLED=false
ENV VITE_SKID2_SIMULATION_ENABLED=${VITE_SKID2_SIMULATION_ENABLED}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine AS production
LABEL org.opencontainers.image.title="Carbon DC Skid 2 Panel" \
      org.opencontainers.image.description="Unprivileged static runtime for the Skid 2 operator panel"
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
