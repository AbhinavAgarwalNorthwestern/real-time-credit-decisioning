#!/bin/bash

#Builds a docker image for the given DockerFile and pushes it to the Docker registry
#given by the env variable

image_name=$1
env=$2

#Just checking if the user has provided the correct number of arguments

if [ -z "$image_name" ]; then
    echo "Usage: $0 <image_name> <env>"
    exit 1
fi

if [ -z "$env" ]; then
    echo "Usage: $0 <image_name> <env>"
    exit 1
fi

#Check if the env variable is set to dev or prod
if [ "$env" != "dev" ] && [ "$env" != "prod" ]; then
    echo "Usage: $0 <image_name> <env>"
    exit 1
fi

if [ "$env" == "dev" ]; then
    echo "Building image ${image_name} for dev environment"
    docker build -t ${image_name}:dev -f Docker/${image_name}.DockerFile .
    kind load docker-image ${image_name}:dev --name rwml-34fa
else
    echo "Building image ${image_name} for prod environment"
    BUILD_DATE=$(date +%s)
	docker buildx build --push \
        --platform linux/amd64 \
        -t ghcr.io/abhinavagarwalnorthwestern/${image_name}:0.1.5-beta.${BUILD_DATE}  \
        --label org.opencontainers.image.revision=$(git rev-parse HEAD) \
        --label org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
        --label org.opencontainers.image.url="https://github.com/Real-World-ML/real-time-ml-system-cohort-4/docker/${image_name}.DockerFile" \
        --label org.opencontainers.image.title="${image_name}" \
        --label org.opencontainers.image.description="${image_name} DockerFile" \
        --label org.opencontainers.image.licenses="" \
        --label org.opencontainers.image.source="https://github.com/Real-World-ML/real-time-ml-system-cohort-4" \
        -f docker/${image_name}.DockerFile .
fi
