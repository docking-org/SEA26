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

ADD environment.yml run.sh ./
RUN conda env create -f environment.yml
RUN echo "conda activate sea25" > ~/.bashrc
ENV PATH /opt/conda/envs/sea25/bin:$PATH


COPY backend ./backend
COPY --from=frontend /app/frontend/out ./frontend/out



RUN chmod +x run.sh
EXPOSE 5050
ENTRYPOINT "./run.sh"


