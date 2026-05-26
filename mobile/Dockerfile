# Multi-stage Next.js build — produces a minimal standalone image.
# NEXT_PUBLIC_INTELLIGENCE_API_URL is baked in at build time.
# Runtime secrets are not needed in this image.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_INTELLIGENCE_API_URL=https://intelligence.decifertrading.com
ENV NEXT_PUBLIC_INTELLIGENCE_API_URL=$NEXT_PUBLIC_INTELLIGENCE_API_URL
# Bot API not used by the customer page — set to empty to avoid local-IP leaks
ENV NEXT_PUBLIC_BOT_API_URL=""
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Next.js standalone includes its own node_modules subset
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
