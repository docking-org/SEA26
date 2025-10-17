#! /bin/bash

docker build --no-cache -t sea25 .
docker run -p 5050:5050 sea25