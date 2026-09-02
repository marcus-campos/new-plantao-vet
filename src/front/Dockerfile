# Web do PlantãoVet: build estático servido por nginx.
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# O Vite injeta a URL da API no build; o navegador roda no host, então localhost serve.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
# Push no navegador. Vazio = o botão "receber alertas" não aparece, e nada
# quebra: é o estado do build de desenvolvimento.
ARG VITE_FIREBASE_CONFIG=""
ARG VITE_FIREBASE_VAPID_KEY=""
ENV VITE_FIREBASE_CONFIG=$VITE_FIREBASE_CONFIG
ENV VITE_FIREBASE_VAPID_KEY=$VITE_FIREBASE_VAPID_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
