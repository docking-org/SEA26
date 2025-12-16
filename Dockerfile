# Frontend build stage
FROM node:lts AS frontend
WORKDIR /app/frontend

ENV PATH /app/frontend/node_modules/.bin:$PATH
COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Backend build stage
FROM condaforge/miniforge3:latest as backend
WORKDIR /home/sea
RUN apt-get update && apt-get install -y curl


RUN apt-get install -y gcc g++ 
RUN apt-get install -y git

ADD requirements.txt run.sh ./
RUN conda create -c conda-forge -n sea25 rdkit python=3.12 -y
RUN echo "conda activate sea25" > ~/.bashrc
ENV PATH /opt/conda/envs/sea25/bin:$PATH
RUN pip install -r requirements.txt

RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_16.x | bash - && \
    apt-get install -y nodejs


COPY backend ./backend

COPY --from=frontend /app/frontend/out ./frontend/out

EXPOSE 3001 5050

RUN chmod +x run.sh
ENTRYPOINT "./run.sh"


