# Stage 1: 安装依赖
FROM node:22-alpine AS deps
WORKDIR /app

ARG NPM_CONFIG_REGISTRY=https://registry.npmmirror.com

COPY package.json package-lock.json ./
RUN npm config set registry "${NPM_CONFIG_REGISTRY}" && npm ci

# Stage 2: 构建应用
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_BASE_URL=/api
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

RUN npm run build

# Stage 3: 生产运行
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# 生产镜像只需 node 运行 server.js,移除 npm/corepack 以消除其捆绑依赖漏洞
RUN rm -rf /usr/local/lib/node_modules /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
