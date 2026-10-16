FROM node:22-bookworm-slim
ENV NODE_ENV=production
WORKDIR /srv/qr
COPY qr-bridge/package.json ./package.json
RUN npm install --omit=dev --no-audit --no-fund
COPY qr-bridge/src/server.mjs ./src/server.mjs
RUN mkdir -p /data/auth
EXPOSE 3000
CMD ["node","src/server.mjs"]
