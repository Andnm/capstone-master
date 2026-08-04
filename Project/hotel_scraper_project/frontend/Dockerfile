# Build Stage
FROM node:20-alpine as build-stage
WORKDIR /app

# Copy package files
COPY package*.json ./
RUN npm install

# Copy source code and .env file
COPY . .

# Build với .env file (Vite sẽ tự động đọc .env)
RUN npm run build

# Production Stage
FROM nginx:stable-alpine as production-stage
COPY --from=build-stage /app/dist /usr/share/nginx/html
# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
