# Frontend build stage
FROM node:lts AS frontend
WORKDIR /app/frontend

ENV PATH /app/frontend/node_modules/.bin:$PATH
COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Backend build stage
FROM conda/miniconda3:latest

WORKDIR /app/backend
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*


RUN apt-get install -y gcc g++ 
RUN apt-get install -y git

COPY environment.yml ./
RUN conda env create -f environment.yml

COPY backend/ ./

COPY --from=frontend /app/frontend/.next /app/backend/frontend/.next
COPY --from=frontend /app/frontend/public /app/backend/frontend/public

EXPOSE 5050

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5050"]
