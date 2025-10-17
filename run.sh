#!/bin/bash

set -m
echo $PWD
ls
uvicorn backend.app.api:app --host 0.0.0.0 --port 5050
