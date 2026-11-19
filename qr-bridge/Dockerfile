FROM node:22-bookworm-slim
ENV NODE_ENV=production
WORKDIR /srv/qr
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY scripts/patch-baileys-acks.mjs ./scripts/patch-baileys-acks.mjs
RUN node scripts/patch-baileys-acks.mjs
COPY src/ ./src/
ARG ORION_BUILD_COMMIT
ENV ORION_BUILD_COMMIT=${ORION_BUILD_COMMIT}
RUN mkdir -p /data/auth
EXPOSE 3000
CMD ["node","src/server.mjs"]
