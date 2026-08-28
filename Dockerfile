# Frontend build stage
FROM node:lts AS frontend
WORKDIR /app/frontend

ARG VITE_CHEMBL_VERSION
ENV VITE_CHEMBL_VERSION=$VITE_CHEMBL_VERSION

ENV PATH /app/frontend/node_modules/.bin:$PATH
COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Backend build stage
FROM condaforge/miniforge3:latest as backend
WORKDIR /home/sea
RUN apt-get update && apt-get install -y curl gcc g++ git


ADD requirements.txt run.sh ./
RUN conda create -c conda-forge -n sea25 rdkit python=3.12 -y
RUN echo "conda activate sea25" > ~/.bashrc
ENV PATH /opt/conda/envs/sea25/bin:$PATH
RUN pip install -r requirements.txt

RUN echo "/usr/lib64" > /etc/ld.so.conf.d/slurm.conf && ldconfig
RUN groupadd -g 44264 slurm && useradd -r -u 44263 -g slurm -s /usr/sbin/nologin slurm \
    && groupadd -g 44266 munge && useradd -r -u 44265 -g munge -s /usr/sbin/nologin munge


COPY backend ./backend

COPY --from=frontend /app/frontend/dist ./frontend/dist

EXPOSE 5050


