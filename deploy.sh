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

# Validate required environment variables early for clearer failures.
REQUIRED_ENV_VARS=(
    RESOURCE_GROUP
    CONTAINER_REGISTRY
    IMAGE_NAME
    CONTAINER_APP_NAME
    REGION
    AZURE_VOICE_LIVE_ENDPOINT
    VOICE_LIVE_MODEL
)

MISSING_VARS=()
for var_name in "${REQUIRED_ENV_VARS[@]}"; do
    if [ -z "${!var_name:-}" ]; then
        MISSING_VARS+=("${var_name}")
    fi
done

if [ "${#MISSING_VARS[@]}" -gt 0 ]; then
    echo "❌ Missing required environment variables in .env:"
    for missing in "${MISSING_VARS[@]}"; do
        echo "   - ${missing}"
    done
    echo ""
    echo "Populate the missing values in .env and rerun deploy.sh."
    exit 1
fi

# Derived values
CONTAINER_REGISTRY_FQDN="${CONTAINER_REGISTRY}.azurecr.io"
CONTAINER_APPS_ENV="${CONTAINER_APP_NAME}-env"
VOICE_LIVE_ENDPOINT_HOST="$(printf '%s' "${AZURE_VOICE_LIVE_ENDPOINT}" | sed -E 's#^https?://##; s#/.*$##')"

# Prefer immutable image tags. If TAG is empty or set to "latest", generate a unique tag.
if [ -z "${TAG:-}" ] || [ "${TAG}" = "latest" ]; then
    TAG_PREFIX="${TAG_PREFIX:-build}"
    GIT_SHA="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "nogit")"
    TAG="${TAG_PREFIX}-$(date -u +%Y%m%d%H%M%S)-${GIT_SHA}"
    echo "ℹ️  TAG was empty or 'latest'; generated immutable image tag: ${TAG}"
fi

ensure_role_assignment() {
    local principal_id="$1"
    local scope="$2"
    local role_name="$3"

    local existing_assignments
    existing_assignments=$(az role assignment list \
        --assignee-object-id "${principal_id}" \
        --scope "${scope}" \
        --query "[?roleDefinitionName=='${role_name}'] | length(@)" \
        -o tsv 2>/dev/null || echo "0")

    if [ "${existing_assignments}" != "0" ]; then
        echo "   Role already assigned: ${role_name}"
        return 0
    fi

    if az role assignment create \
        --assignee-object-id "${principal_id}" \
        --assignee-principal-type ServicePrincipal \
        --role "${role_name}" \
        --scope "${scope}" \
        --output none 2>/dev/null; then
        echo "   Assigned role: ${role_name}"
    else
        echo "   ⚠️  Failed to assign role '${role_name}'."
        echo "      Ensure your Azure account can create role assignments for ${scope}."
    fi
}

echo "🚀 Deploying Voice Live Avatar to Azure Container Apps"
echo "   Resource Group:  ${RESOURCE_GROUP}"
echo "   Registry:        ${CONTAINER_REGISTRY_FQDN}"
echo "   Image:           ${IMAGE_NAME}:${TAG}"
echo "   Container App:   ${CONTAINER_APP_NAME}"
echo "   Region:          ${REGION}"
echo ""

# ── 1. Ensure containerapp CLI extension ──
echo "🔧 Ensuring containerapp CLI extension..."

AZ_CLI_VERSION=$(az version --query '"azure-cli"' -o tsv 2>/dev/null || az --version | awk '/azure-cli/ {print $2; exit}')
REQUIRED_AZ_CLI_VERSION="2.45.0"

# Container Apps commands require a modern Azure CLI.
if ! printf '%s\n%s\n' "${REQUIRED_AZ_CLI_VERSION}" "${AZ_CLI_VERSION}" | sort -V -C; then
    echo "❌ Azure CLI ${AZ_CLI_VERSION} is too old for Container Apps."
    echo "   Required version: >= ${REQUIRED_AZ_CLI_VERSION}"
    echo "   Please upgrade Azure CLI, then re-run this script."
    exit 1
fi

if ! az containerapp --help >/dev/null 2>&1; then
    echo "ℹ️  'az containerapp' not currently available. Installing extension..."
    if az extension show --name containerapp >/dev/null 2>&1; then
        az extension update --name containerapp >/dev/null 2>&1 || true
    else
        az extension add --name containerapp --yes
    fi
fi

if ! az containerapp --help >/dev/null 2>&1; then
    echo "❌ 'az containerapp' is still unavailable after extension install."
    echo "   Azure CLI version: $(az version --query '"azure-cli"' -o tsv 2>/dev/null || echo unknown)"
    echo "   Try updating Azure CLI and re-running:"
    echo "   - az upgrade --yes"
    echo "   - az extension add --name containerapp --yes"
    exit 1
fi

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

# Optionally keep a mutable alias for manual testing or backward compatibility.
if [ "${PUSH_LATEST_ALIAS:-false}" = "true" ]; then
    echo "🏷️  Also pushing mutable alias: ${IMAGE_NAME}:latest"
    docker tag "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:${TAG}" "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:latest"
    docker push "${CONTAINER_REGISTRY_FQDN}/${IMAGE_NAME}:latest"
fi

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
    --max-replicas 5 \
    --env-vars \
        "AZURE_VOICE_LIVE_ENDPOINT=${AZURE_VOICE_LIVE_ENDPOINT}" \
        "VOICE_LIVE_MODEL=${VOICE_LIVE_MODEL}" \
        "AZURE_VOICE_AVATAR_CHARACTER=${AZURE_VOICE_AVATAR_CHARACTER}" \
        "AZURE_VOICE_AVATAR_CUSTOMIZED=${AZURE_VOICE_AVATAR_CUSTOMIZED}" \
        "AZURE_VOICE_AVATAR_WIDTH=${AZURE_VOICE_AVATAR_WIDTH}" \
        "AZURE_VOICE_AVATAR_HEIGHT=${AZURE_VOICE_AVATAR_HEIGHT}" \
        "AZURE_VOICE_AVATAR_BITRATE=${AZURE_VOICE_AVATAR_BITRATE}" \
        "AZURE_TTS_VOICE=${AZURE_TTS_VOICE}" \
        "AZURE_VOICE_SOURCE=${AZURE_VOICE_SOURCE}" \
        "AZURE_OPENAI_API_KEY=${AZURE_OPENAI_API_KEY}" \
    --output none

# ── 9. Enable managed identity for Azure SDK authentication ──
echo "🔐 Enabling managed identity for ${CONTAINER_APP_NAME}..."
az containerapp identity assign \
    --name "${CONTAINER_APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --system-assigned \
    --output none

# ── 10. Assign Voice Live RBAC roles to the managed identity when possible ──
echo "🔐 Ensuring Voice Live RBAC assignments..."
CONTAINER_APP_PRINCIPAL_ID=$(az containerapp show \
    --name "${CONTAINER_APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --query identity.principalId -o tsv 2>/dev/null || true)
VOICE_LIVE_RESOURCE_ID=$(az cognitiveservices account list \
    --query "[?contains(properties.endpoint, '${VOICE_LIVE_ENDPOINT_HOST}')].id | [0]" \
    -o tsv 2>/dev/null || true)

if [ -n "${CONTAINER_APP_PRINCIPAL_ID}" ] && [ -n "${VOICE_LIVE_RESOURCE_ID}" ]; then
    echo "   Matched Voice Live resource: ${VOICE_LIVE_RESOURCE_ID}"
    ensure_role_assignment "${CONTAINER_APP_PRINCIPAL_ID}" "${VOICE_LIVE_RESOURCE_ID}" "Cognitive Services User"
    ensure_role_assignment "${CONTAINER_APP_PRINCIPAL_ID}" "${VOICE_LIVE_RESOURCE_ID}" "Cognitive Services OpenAI User"
    echo "   RBAC propagation can take a few minutes after deployment."
else
    echo "   ⚠️  Could not automatically determine RBAC scope for ${AZURE_VOICE_LIVE_ENDPOINT}."
    echo "      If you use managed identity, assign these roles manually on the matching Cognitive Services resource:"
    echo "      - Cognitive Services User"
    echo "      - Cognitive Services OpenAI User"
fi

# ── 11. Get the app URL ──
APP_URL=$(az containerapp show \
    --name "${CONTAINER_APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "✅ Deployment complete!"
echo "🔗 App URL: https://${APP_URL}"
echo ""
echo "📝 Note: The Container App has a system-assigned managed identity."
echo "   The script attempted to assign 'Cognitive Services User' and"
echo "   'Cognitive Services OpenAI User' on the matching Voice Live resource."
echo "   If session creation still fails with 401/403, wait a few minutes for"
echo "   RBAC propagation and verify the role assignments in Azure."