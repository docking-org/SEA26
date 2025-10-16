# Frontend build stage
FROM node:lts AS frontend
WORKDIR /app/frontend

ENV PATH /app/frontend/node_modules/.bin:$PATH
COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build
RUN npm start &
# Backend build stage
FROM condaforge/miniforge3:latest as backend

WORKDIR /app/backend
RUN apt-get update && apt-get install -y curl


RUN apt-get install -y gcc g++ 
RUN apt-get install -y git

COPY environment.yml ./
RUN conda env create -f environment.yml

COPY backend/ ./

COPY run.sh ./
RUN chmod +x run.sh

# COPY --from=frontend /app/frontend/.next /app/backend/frontend/.next
# COPY --from=frontend /app/frontend/public /app/backend/frontend/public

EXPOSE 5050 3000
CMD ["./run.sh"]

