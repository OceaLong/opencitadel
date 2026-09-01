FROM node:22-alpine

ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm cache clean --force

COPY lib ./lib
COPY server.mjs ./server.mjs

USER node

EXPOSE 8080

CMD ["node", "server.mjs"]
