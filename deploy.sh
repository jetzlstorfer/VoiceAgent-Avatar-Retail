#!/bin/bash

# Azure Container App Deployment Script
# Sources all configuration from .env in the repo root.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration from .env
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
else
    echo "❌ .env file not found in ${SCRIPT_DIR}"
    exit 1
fi

# Derived values
CONTAINER_REGISTRY_FQDN="${CONTAINER_REGISTRY}.azurecr.io"
CONTAINER_APPS_ENV="${CONTAINER_APP_NAME}-env"

echo "🚀 Deploying Voice Live Avatar to Azure Container Apps"
echo "   Resource Group:  ${RESOURCE_GROUP}"
echo "   Registry:        ${CONTAINER_REGISTRY_FQDN}"
echo "   Image:           ${IMAGE_NAME}:${TAG}"
echo "   Container App:   ${CONTAINER_APP_NAME}"
echo "   Region:          ${REGION}"
echo ""

# ── 1. Ensure containerapp CLI extension ──
echo "🔧 Ensuring containerapp CLI extension..."
az extension add --name containerapp --upgrade --yes 2>/dev/null || true

# ── 2. Register required providers ──
echo "🔧 Registering resource providers..."
az provider register --namespace Microsoft.App --wait 2>/dev/null || true
az provider register --namespace Microsoft.OperationalInsights --wait 2>/dev/null || true

# ── 3. Create resource group (idempotent) ──
echo "📁 Ensuring resource group ${RESOURCE_GROUP}..."
az group create --name "${RESOURCE_GROUP}" --location "${REGION}" --output none

# ── 4. Create ACR (idempotent) ──
echo "📦 Ensuring container registry ${CONTAINER_REGISTRY}..."
az acr create \
    --name "${CONTAINER_REGISTRY}" \
    --resource-group "${RESOURCE_GROUP}" \
    --sku Basic \
    --admin-enabled true \
    --output none 2>/dev/null || true

# ── 5. Build & push Docker image ──
echo "📦 Building Docker image..."
docker build -t "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:${TAG}" .

echo "🔐 Logging into ACR..."
az acr login --name "${CONTAINER_REGISTRY}"

echo "🔐 Pushing image to ACR..."
docker push "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:${TAG}"

# ── 6. Create Container Apps environment (idempotent) ──
echo "🌐 Ensuring Container Apps environment ${CONTAINER_APPS_ENV}..."
az containerapp env create \
    --name "${CONTAINER_APPS_ENV}" \
    --resource-group "${RESOURCE_GROUP}" \
    --location "${REGION}" \
    --output none 2>/dev/null || true

# ── 7. Get ACR credentials ──
ACR_USERNAME=$(az acr credential show --name "${CONTAINER_REGISTRY}" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "${CONTAINER_REGISTRY}" --query "passwords[0].value" -o tsv)

# ── 8. Deploy / update Container App ──
echo "🚀 Deploying container app ${CONTAINER_APP_NAME}..."
az containerapp create \
    --name "${CONTAINER_APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --environment "${CONTAINER_APPS_ENV}" \
    --image "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:${TAG}" \
    --registry-server "${CONTAINER_REGISTRY_FQDN}" \
    --registry-username "${ACR_USERNAME}" \
    --registry-password "${ACR_PASSWORD}" \
    --target-port 8000 \
    --ingress external \
    --cpu 1.0 \
    --memory 2.0Gi \
    --min-replicas 1 \
    --max-replicas 10 \
    --env-vars \
        "AZURE_VOICE_LIVE_ENDPOINT=${AZURE_VOICE_LIVE_ENDPOINT}" \
        "VOICE_LIVE_MODEL=${VOICE_LIVE_MODEL}" \
        "AZURE_VOICE_AVATAR_CHARACTER=${AZURE_VOICE_AVATAR_CHARACTER}" \
        "AZURE_VOICE_AVATAR_CUSTOMIZED=${AZURE_VOICE_AVATAR_CUSTOMIZED}" \
        "AZURE_VOICE_AVATAR_WIDTH=${AZURE_VOICE_AVATAR_WIDTH}" \
        "AZURE_VOICE_AVATAR_HEIGHT=${AZURE_VOICE_AVATAR_HEIGHT}" \
        "AZURE_VOICE_AVATAR_BITRATE=${AZURE_VOICE_AVATAR_BITRATE}" \
        "AZURE_TTS_VOICE=${AZURE_TTS_VOICE}" \
    --output none

# ── 9. Get the app URL ──
APP_URL=$(az containerapp show \
    --name "${CONTAINER_APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "✅ Deployment complete!"
echo "🔗 App URL: https://${APP_URL}"