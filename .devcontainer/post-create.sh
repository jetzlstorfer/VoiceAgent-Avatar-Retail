#!/usr/bin/env bash
set -euo pipefail

echo "[devcontainer] Ensuring Azure CLI is up to date..."
if command -v az >/dev/null 2>&1; then
    CURRENT_AZ_VERSION=$(az version --query '"azure-cli"' -o tsv 2>/dev/null || az --version | awk '/azure-cli/ {print $2; exit}')
    echo "[devcontainer] Current Azure CLI version: ${CURRENT_AZ_VERSION}"
else
    echo "[devcontainer] Azure CLI not found. Installing latest Azure CLI..."
fi

# Install or update to latest Azure CLI for Debian/Ubuntu.
YARN_LIST_FILE="/etc/apt/sources.list.d/yarn.list"
YARN_LIST_BACKUP="/etc/apt/sources.list.d/yarn.list.disabled-by-devcontainer"

# Work around apt failures from unrelated third-party repos (for example a stale Yarn key).
if [ -f "${YARN_LIST_FILE}" ]; then
    echo "[devcontainer] Temporarily disabling Yarn apt source to avoid apt update failures..."
    sudo mv "${YARN_LIST_FILE}" "${YARN_LIST_BACKUP}"
fi

AZ_INSTALL_OK=true
if ! curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash; then
    AZ_INSTALL_OK=false
    echo "[devcontainer] Warning: Azure CLI installer failed."
fi

if [ -f "${YARN_LIST_BACKUP}" ]; then
    echo "[devcontainer] Restoring Yarn apt source..."
    sudo mv "${YARN_LIST_BACKUP}" "${YARN_LIST_FILE}"
fi

if [ "${AZ_INSTALL_OK}" != "true" ]; then
    echo "[devcontainer] Continuing without hard failure so post-create does not abort."
    exit 0
fi

NEW_AZ_VERSION=$(az version --query '"azure-cli"' -o tsv 2>/dev/null || az --version | awk '/azure-cli/ {print $2; exit}')
echo "[devcontainer] Azure CLI version after update: ${NEW_AZ_VERSION}"

echo "[devcontainer] Ensuring containerapp support is available..."
if ! az containerapp --help >/dev/null 2>&1; then
    az extension add --name containerapp --yes || true
fi

if ! az containerapp --help >/dev/null 2>&1; then
    echo "[devcontainer] Warning: 'az containerapp' is still unavailable."
    echo "[devcontainer] Please check network and run: az extension add --name containerapp --yes"
else
    echo "[devcontainer] 'az containerapp' is available."
fi
