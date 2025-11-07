#!/bin/bash

echo "Starting KEPCO Power Planner add-on"

RSA_USER_ID=$(jq --raw-output '.RSA_USER_ID' /data/options.json)
RSA_USER_PWD=$(jq --raw-output '.RSA_USER_PWD' /data/options.json)

export RSA_USER_ID
export RSA_USER_PWD

python3 /app/main.py
