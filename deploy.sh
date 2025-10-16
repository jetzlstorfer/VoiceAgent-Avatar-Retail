#!/bin/bash

# Azure Container App Deployment Script
set -e

# Configuration - Update these values
RESOURCE_GROUP="rg-voice-live-avatar"
CONTAINER_APP_NAME="voice-live-avatar-app"
CONTAINER_REGISTRY="<your-registry>.azurecr.io"
IMAGE_NAME="voice-live-avatar"
TAG="latest"
LOCATION="eastus2"

echo "🚀 Deploying Voice Live Avatar to Azure Container Apps"

# Build and push Docker image
echo "📦 Building Docker image..."
docker build -t ${CONTAINER_REGISTRY}/${IMAGE_NAME}:${TAG} .

echo "🔐 Pushing to registry..."
docker push ${CONTAINER_REGISTRY}/${IMAGE_NAME}:${TAG}

# Deploy to Container Apps
echo "🌐 Deploying to Azure Container Apps..."
az containerapp update \
    --name ${CONTAINER_APP_NAME} \
    --resource-group ${RESOURCE_GROUP} \
    --image ${CONTAINER_REGISTRY}/${IMAGE_NAME}:${TAG}

echo "✅ Deployment complete!"
echo "🔗 App URL: https://${CONTAINER_APP_NAME}.${LOCATION}.azurecontainerapps.io"