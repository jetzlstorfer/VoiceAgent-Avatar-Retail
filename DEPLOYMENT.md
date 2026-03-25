# Azure Deployment Guide

This guide will help you deploy the Voice Agent Avatar Retail application to Azure using Azure Developer CLI (azd).

## 🎯 What Gets Deployed

This deployment creates a complete production-ready infrastructure on Azure:

### Core Services
- **Azure Container Apps** - Hosts the FastAPI backend + React frontend
- **Azure Container Registry** - Stores Docker images
- **Azure OpenAI Service** - GPT-4 Realtime model with Voice Live API
- **Azure AI Speech** - Avatar characters and voice synthesis
- **Azure AI Search** - Knowledge base for QnA retrieval

### Business Services
- **Azure Logic Apps** - Shipment workflow & conversation analysis
- **Azure Cosmos DB** - Conversation history & analysis storage
- **Azure SQL Database** - Order & shipment tracking

### Monitoring
- **Azure Monitor** - Log Analytics workspace
- **Application Insights** - Application telemetry and diagnostics

## 📋 Prerequisites

Before you begin, ensure you have:

1. **Azure Subscription** with appropriate permissions to create resources
2. **Azure Developer CLI (azd)** - [Install azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
3. **Azure CLI** - [Install Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
4. **Docker** - [Install Docker](https://docs.docker.com/get-docker/)
5. **Node.js 20+** - For building the frontend
6. **Git** - For version control

### Verify Installations

```bash
azd version
az --version
docker --version
node --version
```

## 🚀 Quick Start Deployment

### Option 1: Interactive Deployment (Recommended for First-Time)

```bash
# 1. Login to Azure
azd auth login

# 2. Initialize the environment
azd env new

# When prompted:
# - Enter environment name (e.g., "dev", "prod")
# - Select your Azure subscription
# - Choose location (recommend: eastus2, westus2, or northeurope for avatar support)

# 3. Set required configuration
azd env set SQL_ADMIN_USERNAME sqladmin
azd env set SQL_ADMIN_PASSWORD $(openssl rand -base64 32)

# 4. Deploy everything!
azd up
```

The `azd up` command will:
- Provision all Azure infrastructure using Bicep templates
- Build the Docker container (frontend + backend)
- Push the container to Azure Container Registry
- Deploy the application to Azure Container Apps

⏱️ **Estimated time**: 15-20 minutes

### Option 2: Automated Deployment with Configuration File

```bash
# 1. Create environment configuration
cp .azure/.env.template .azure/dev/.env

# 2. Edit .azure/dev/.env and fill in all values
# Pay special attention to:
# - SQL_ADMIN_PASSWORD (generate a strong password)
# - AZURE_LOCATION (choose a region with avatar support)
# - AZURE_VOICE_AVATAR_CHARACTER (verify it exists in your region)

# 3. Login and deploy
azd auth login
azd env select dev  # or your environment name
azd up
```

## 🔧 Configuration Details

### Required Settings

| Setting | Description | Example |
|---------|-------------|---------|
| `AZURE_ENV_NAME` | Environment name | `dev`, `prod` |
| `AZURE_LOCATION` | Azure region | `eastus2`, `westus2` |
| `SQL_ADMIN_USERNAME` | SQL admin username | `sqladmin` |
| `SQL_ADMIN_PASSWORD` | SQL admin password (secure!) | Generate with: `openssl rand -base64 32` |

### Avatar Configuration

**Important**: Avatar character names are **region-specific** and **case-sensitive**.

1. Navigate to [Azure Speech Studio](https://speech.microsoft.com)
2. Select your Speech resource
3. Go to Avatar section
4. Note the **exact character ID** (e.g., `lisa`, `james`, `michelle`)
5. Set `AZURE_VOICE_AVATAR_CHARACTER` to match exactly

Common error: `avatar_verification_failed` means the character doesn't exist in your region.

### Regional Availability

Not all Azure regions support all features. Recommended regions:

| Region | OpenAI Realtime | Speech Avatar | AI Search |
|--------|----------------|---------------|-----------|
| **eastus2** | ✅ | ✅ | ✅ |
| **westus2** | ✅ | ✅ | ✅ |
| **northeurope** | ✅ | ✅ | ✅ |
| **swedencentral** | ✅ | ⚠️ Limited | ✅ |

## 📦 Understanding the Deployment Process

### Infrastructure as Code (Bicep)

All infrastructure is defined in Bicep templates under `infra/`:

```
infra/
├── main.bicep                    # Main orchestration template
├── main.parameters.json          # Parameter mappings
├── abbreviations.json            # Resource naming conventions
└── modules/
    ├── monitoring.bicep          # Log Analytics + App Insights
    ├── container-registry.bicep  # Container Registry
    ├── openai.bicep             # Azure OpenAI with GPT-4 Realtime
    ├── speech.bicep             # Azure AI Speech
    ├── search.bicep             # Azure AI Search
    ├── cosmos.bicep             # Cosmos DB + containers
    ├── sql.bicep                # SQL Server + database
    ├── logic-apps.bicep         # Logic App workflows
    ├── container-apps-environment.bicep  # Container Apps env
    └── container-app.bicep      # Container App deployment
```

### Build Process

1. **Frontend Build** (`frontend/`)
   - Runs `npm install && npm run build:prod`
   - Creates optimized production bundle
   - Outputs to `frontend/dist/`

2. **Docker Multi-Stage Build** (`Dockerfile`)
   - Stage 1: Builds frontend with Node.js
   - Stage 2: Copies frontend to backend, creates Python container
   - Final image contains both frontend and backend

3. **Container Deployment**
   - Pushes image to Azure Container Registry
   - Container App pulls and runs the image
   - FastAPI serves both API and static frontend files

## 🔐 Security Best Practices

### Secrets Management

All secrets are stored securely:
- **Bicep deployment**: Uses `@secure()` parameters
- **Container App**: Stores as secrets, referenced by environment variables
- **Logic Apps**: Uses API Connections with managed credentials

### Access Control

The deployment uses:
- **System-assigned Managed Identity** for the Container App
- **Role-Based Access Control (RBAC)** for Azure resources
- **API Keys** stored as Container App secrets

For Azure Voice Live authentication with managed identity, the Container App identity needs these roles on the matching Cognitive Services account:
- **Cognitive Services User**
- **Cognitive Services OpenAI User**

The repository's `deploy.sh` now tries to assign both roles automatically after enabling the Container App identity, as long as it can match `AZURE_VOICE_LIVE_ENDPOINT` to a Cognitive Services resource in the current subscription. Your deployment identity still needs permission to create role assignments, such as `Owner` or `User Access Administrator`.

### Network Security

- Container App has public ingress (HTTPS only)
- SQL Database allows Azure services (can be restricted post-deployment)
- All communication uses TLS/SSL

### Post-Deployment Hardening

```bash
# 1. Restrict SQL firewall to specific IPs
az sql server firewall-rule create \
  --resource-group <resource-group> \
  --server <sql-server> \
  --name AllowMyIP \
  --start-ip-address <your-ip> \
  --end-ip-address <your-ip>

# 2. Enable Azure AD authentication
az sql server ad-admin create \
  --resource-group <resource-group> \
  --server <sql-server> \
  --display-name <admin-name> \
  --object-id <object-id>

# 3. Configure Custom Domain (optional)
az containerapp hostname add \
  --resource-group <resource-group> \
  --name <container-app> \
  --hostname <your-domain.com>
```

## 🧪 Post-Deployment Steps

### 1. Verify Deployment

```bash
# Get the application URL
azd env get-value AZURE_CONTAINER_APP_URL

# Test the health endpoint
curl $(azd env get-value AZURE_CONTAINER_APP_URL)/health

# Test liveness and readiness probes (useful for Kubernetes/container health checks)
curl $(azd env get-value AZURE_CONTAINER_APP_URL)/health/live
curl $(azd env get-value AZURE_CONTAINER_APP_URL)/health/ready

# View all deployed resource URLs
azd env get-values
```

If session creation fails with `401`, verify managed identity RBAC:

```bash
az role assignment list \
  --assignee-object-id <container-app-principal-id> \
  --scope <cognitive-services-resource-id> \
  --query "[].roleDefinitionName"
```

Expected roles:
- `Cognitive Services User`
- `Cognitive Services OpenAI User`

### 2. Initialize SQL Database

The database schema is created automatically by the post-deployment script, but you can also run it manually:

```bash
# Connect to SQL Database
az sql db show-connection-string \
  --client sqlcmd \
  --name $(azd env get-value AZURE_SQL_DATABASE_NAME) \
  --server $(azd env get-value AZURE_SQL_SERVER_NAME)

# Execute schema from infra/post-deploy.sh
```

### 3. Set Up AI Search Index

```bash
# Option A: Use Azure Portal
# 1. Navigate to Azure AI Search in Azure Portal
# 2. Create a new index with the name from AZURE_SEARCH_INDEX_NAME
# 3. Upload your documents (product manuals, FAQs, policies)
# 4. Configure semantic search

# Option B: Use Azure SDK (example script)
cd backend
python -c "
from azure.search.documents.indexes import SearchIndexClient
from azure.core.credentials import AzureKeyCredential
# ... create index programmatically
"
```

### 4. Test the Application

```bash
# Open the application URL in your browser
open $(azd env get-value AZURE_CONTAINER_APP_URL)

# Check Application Insights for telemetry
az monitor app-insights component show \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) \
  --query "[0].appId" -o tsv
```

## 📊 Monitoring and Troubleshooting

### View Application Logs

```bash
# Real-time logs
az containerapp logs show \
  --name $(azd env get-value AZURE_CONTAINER_APP_NAME) \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) \
  --follow

# Recent logs
azd monitor --logs
```

### View Application Insights

```bash
# Open Application Insights in Azure Portal
az portal

# Or use direct query
az monitor app-insights query \
  --app $(azd env get-value AZURE_CONTAINER_APP_NAME) \
  --analytics-query "requests | summarize count() by resultCode"
```

### Common Issues

#### Issue: Avatar connection fails with `avatar_verification_failed`

**Solution**: Verify the character name exists in your Speech region
```bash
# Check your configuration
azd env get-values | grep AVATAR

# Update if needed
azd env set AZURE_VOICE_AVATAR_CHARACTER "lisa"
azd deploy
```

#### Issue: Container App fails to start

**Solution**: Check container logs for errors
```bash
# View container logs
az containerapp logs show \
  --name $(azd env get-value AZURE_CONTAINER_APP_NAME) \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP) \
  --tail 100
```

#### Issue: OpenAI quota exceeded

**Solution**: Check and increase quota
```bash
# Check current quota
az cognitiveservices account deployment list \
  --name $(azd env get-value AZURE_OPENAI_NAME) \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP)

# Request quota increase in Azure Portal
```

## 🔄 Update and Redeploy

### Update Application Code

```bash
# After making code changes
azd deploy

# This will:
# 1. Rebuild the Docker container
# 2. Push to Container Registry
# 3. Update Container App to use new image
```

### Update Infrastructure

```bash
# After modifying Bicep files
azd provision

# Or do both at once
azd up
```

### Update Environment Variables

```bash
# Update a single value
azd env set AZURE_VOICE_AVATAR_CHARACTER "james"

# Redeploy to apply changes
azd deploy
```

## 🗑️ Cleanup

### Delete All Resources

```bash
# Delete the entire environment
azd down

# This will delete:
# - All Azure resources in the resource group
# - The local environment configuration
```

### Delete Only the Application

```bash
# Keep infrastructure, delete only Container App
az containerapp delete \
  --name $(azd env get-value AZURE_CONTAINER_APP_NAME) \
  --resource-group $(azd env get-value AZURE_RESOURCE_GROUP)
```

## 💰 Cost Estimation

Estimated monthly costs (Pay-as-you-go pricing):

| Service | Configuration | Est. Cost/Month |
|---------|--------------|-----------------|
| Container Apps | 1-2 replicas, 1 vCPU, 2GB RAM | $50-100 |
| Azure OpenAI | GPT-4 Realtime, 100K TPM | $100-500 (usage-based) |
| Azure AI Speech | Voice + Avatar | $50-200 (usage-based) |
| Azure AI Search | Standard tier | $250 |
| Cosmos DB | 400 RU/s | $25 |
| SQL Database | Basic tier | $5 |
| Logic Apps | Standard | $10-50 (usage-based) |
| Monitoring | Log Analytics + App Insights | $10-50 |
| **Total** | | **$500-1,200/month** |

> **Note**: Actual costs depend heavily on usage. OpenAI and Speech are pay-per-use.

### Cost Optimization Tips

1. **Use Azure Dev/Test pricing** if eligible
2. **Enable Azure Hybrid Benefit** for SQL if you have licenses
3. **Scale down** Container Apps when not in use: `az containerapp update --min-replicas 0`
4. **Use Azure Free tier** for development: Some services offer free tiers
5. **Monitor usage** with Azure Cost Management

## 🔗 Additional Resources

- [Azure Developer CLI Documentation](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
- [Azure OpenAI Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-api-reference)
- [Azure Container Apps Documentation](https://learn.microsoft.com/azure/container-apps/)
- [Azure AI Search Documentation](https://learn.microsoft.com/azure/search/)
- [Bicep Documentation](https://learn.microsoft.com/azure/azure-resource-manager/bicep/)

## 📞 Support

- **Repository Issues**: [GitHub Issues](https://github.com/MSFT-Innovation-Hub-India/VoiceAgent-Avatar-Retail/issues)
- **Azure Support**: [Azure Portal Support](https://portal.azure.com/#blade/Microsoft_Azure_Support/HelpAndSupportBlade)
- **Azure Developer CLI**: [GitHub Discussions](https://github.com/Azure/azure-dev/discussions)
