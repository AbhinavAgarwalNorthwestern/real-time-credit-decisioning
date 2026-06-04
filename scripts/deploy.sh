#!/bin/bash

# This script deploys a given service to the given Kubernetes environment


service=$1
env=$2

#Just checking if the user has provided the correct number of arguments

if [ -z "$service" ]; then
    echo "Usage: $0 <service> <env>"
    exit 1
fi


#Check if the env variable is set to dev or prod
if [ "$env" != "dev" ] && [ "$env" != "prod" ]; then
    echo "env must be either dev or prod"
    exit 1
fi

cd deployments/${env}
#hook the direnv tool here
#We add this line here so that direnv can load the correct KUBECONFIG environment
#file from the deployments/${env}/.env.local file

eval "$(direnv export bash)"
echo "KUBECONFIG=${KUBECONFIG}"

#if there is a kustomization.yaml file, use kustomize to deply service
if [ -f "${service}/kustomization.yaml" ]; then
    echo "Using kustomize to deploy ${service} in ${env} environment"
    # delete the service
    # TODO: add the ignore-not-found flag to avoid errors the first time you deploy something
    kustomize build ${service} | kubectl delete -f -
    kustomize build ${service} | kubectl apply -f -

else
    echo "No kustomization.yaml found for ${service}, using kubectl apply"
    #If there is no kustomization.yaml file, use kubectl apply to deploy the service
    #manually apply the manifests deployment
    kubectl delete -f ${service}/${service}-d.yaml --ignore-not-found=true
    kubectl apply -f ${service}/${service}-d.yaml

fi
