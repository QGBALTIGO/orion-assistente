FROM node:22-bookworm-slim
ENV NODE_ENV=production
WORKDIR /srv/qr
COPY package.json ./package.json
RUN npm install --omit=dev --no-audit --no-fund
COPY src/ ./src/
RUN mkdir -p /data/auth
EXPOSE 3000
CMD ["node","src/server.mjs"]
