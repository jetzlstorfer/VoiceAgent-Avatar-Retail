# Pre-Deployment Checklist

## ✅ Infrastructure Setup Complete!

Your repository now has complete Azure deployment infrastructure. Use this checklist to ensure everything is ready before deploying.

## 📋 Pre-Deployment Requirements

### Azure Account & CLI Installations
- [ ] Azure Subscription (with appropriate permissions)
- [ ] Azure role-assignment permission if using `deploy.sh` with managed identity (`Owner` or `User Access Administrator` recommended)
- [ ] Azure CLI installed (`az --version`)
- [ ] Azure Developer CLI installed (`azd --version`)
- [ ] Docker Desktop installed (`docker --version`)
- [ ] Node.js 20+ installed (`node --version`)
- [ ] Git installed (`git --version`)

### Azure Service Quotas
- [ ] OpenAI quota for GPT-4 Realtime (minimum 100K TPM)
- [ ] Speech service quota in your region
- [ ] Container Apps environment available in your region
- [ ] SQL Database capacity available

### Avatar Character Verification
- [ ] Visit [Speech Studio](https://speech.microsoft.com)
- [ ] Log in with your Azure account
- [ ] Navigate to your Speech resource
- [ ] Check Avatar section for available characters
- [ ] Note the exact character name (lisa, james, michelle, etc.)
- [ ] Note the region limitations

### Regional Planning
- [ ] Choose deployment region (recommend: eastus2, westus2, northeurope)
- [ ] Verify all services available in chosen region:
  - [ ] Azure OpenAI
  - [ ] Azure AI Speech with Avatar
  - [ ] Azure AI Search
  - [ ] Azure Container Apps

## 📁 Files Created

### Documentation
- [x] `DEPLOYMENT.md` - Comprehensive guide (READ THIS!)
- [x] `AZURE_SETUP_SUMMARY.md` - Quick overview
- [x] `AZURE_STRUCTURE_GUIDE.txt` - Visual structure
- [x] `PRE_DEPLOYMENT_CHECKLIST.md` - This file

### Configuration Files
- [x] `azure.yaml` - Azure Developer CLI configuration
- [x] `.azure/.env.template` - Environment variables template
- [x] `infra/main.parameters.json` - Parameter mappings
- [x] `infra/abbreviations.json` - Resource naming

### Deployment Scripts
- [x] `setup-azure.sh` - Interactive deployment
- [x] `infra/post-deploy.sh` - Post-deployment setup

### Infrastructure Templates (Bicep)
- [x] `infra/main.bicep` - Main orchestration
- [x] `infra/modules/monitoring.bicep` - Monitoring services
- [x] `infra/modules/container-registry.bicep` - Container registry
- [x] `infra/modules/openai.bicep` - Azure OpenAI
- [x] `infra/modules/speech.bicep` - Azure Speech
- [x] `infra/modules/search.bicep` - Azure Search
- [x] `infra/modules/cosmos.bicep` - Cosmos DB
- [x] `infra/modules/sql.bicep` - SQL Database
- [x] `infra/modules/logic-apps.bicep` - Logic Apps
- [x] `infra/modules/container-apps-environment.bicep` - Container environment
- [x] `infra/modules/container-app.bicep` - Container app

## 🎯 Pre-Deployment Steps

### 1. Review Documentation
- [ ] Read `DEPLOYMENT.md` completely
- [ ] Understand the deployment flow
- [ ] Note important configuration options
- [ ] Review troubleshooting section

### 2. Gather Required Information
- [ ] Your Azure subscription ID
- [ ] Your preferred region (note availability)
- [ ] Avatar character name (from Speech Studio)
- [ ] Strong SQL admin password (use: `openssl rand -base64 32`)
- [ ] Your organization's naming conventions

### 3. Prepare Environment
- [ ] Create a new Git branch for deployment changes
- [ ] Ensure all application code is committed
- [ ] Test application locally (run locally first!)
- [ ] Ensure Docker builds successfully
- [ ] Verify frontend builds with `npm run build:prod`

### 4. Verify Prerequisites
```bash
azd version          # Should be 1.0.0 or later
az --version         # Should be 2.50.0 or later
docker --version     # Should be 20.10+
node --version       # Should be 20.0.0+
npm --version        # Should be 10.0.0+
```

## 📝 Configuration Decisions

### Resource Naming
- [ ] Confirm naming strategy (follows Azure conventions)
- [ ] Plan resource names (will be auto-generated with resourceToken)
- [ ] Decide on environment name (dev, test, prod, etc.)

### Service Tiers
- [ ] Container Apps: Basic (can scale up later)
- [ ] SQL Database: Basic (can upgrade later)
- [ ] Cosmos DB: Provisioned throughput (400 RU/s)
- [ ] Azure Search: Standard tier
- [ ] Azure OpenAI: Standard tier with 100K TPM

### Security Configuration
- [ ] Plan network access (default: public)
- [ ] Decide on authentication method
- [ ] Plan for secrets management
- [ ] Note any compliance requirements
- [ ] If using managed identity for Voice Live, confirm the deployment identity can assign `Cognitive Services User` and `Cognitive Services OpenAI User`

## 🚀 Deployment Methods

### Option A: Interactive Setup (Recommended)
```bash
./setup-azure.sh
# This will guide you through the entire process
```

### Option B: Manual Step-by-Step
```bash
# 1. Authenticate
azd auth login

# 2. Create environment
azd env new
# Choose: dev (or your environment name)
# Choose: your subscription
# Choose: eastus2 (or your region)

# 3. Configure
azd env set SQL_ADMIN_PASSWORD $(openssl rand -base64 32)
azd env set AZURE_VOICE_AVATAR_CHARACTER "lisa"

# 4. Deploy
azd up
# This will provision and deploy everything
```

## ⏱️ Expected Timings

| Step | Time |
|------|------|
| Prerequisites setup | 5-10 min |
| Azure authentication | 2-3 min |
| Environment creation | 1 min |
| Building frontend | 3-5 min |
| Building Docker container | 5-10 min |
| Infrastructure deployment | 10-15 min |
| Application startup | 2-3 min |
| **Total** | **~30-45 min** |

## 📊 Cost Considerations Before Deploying

**Monthly Cost Estimate**: $500-1,200

Main cost drivers:
- Azure OpenAI usage (pay-per-token)
- Azure Speech API calls (pay-per-minute)
- Azure Search (fixed $250/month Basic tier)
- Cosmos DB throughput (400 RU/s = ~$25/month)

**Cost optimization tips**:
- [ ] Use free tier if eligible
- [ ] Set up budget alerts in Azure Portal
- [ ] Monitor usage daily first week
- [ ] Scale Container Apps down when not in use
- [ ] Delete resources when experimenting

## 🔐 Security Considerations

Before deployment:
- [ ] Use strong SQL password (provided by script)
- [ ] Don't commit `.env` files (use `.env.template` only)
- [ ] Review secret management in Container App
- [ ] Plan for RBAC after deployment
- [ ] Consider private endpoint needs
- [ ] Plan for Azure AD/Entra ID integration

## 🧪 Post-Deployment Testing

After successful deployment:
- [ ] Test health endpoint: `curl <app-url>/health`
- [ ] Open application in browser
- [ ] Check Application Insights for errors
- [ ] Verify database connections
- [ ] Test Logic App webhook endpoints
- [ ] Verify avatar connection

## 📞 Getting Help

If deployment fails:
1. [ ] Check error messages in terminal
2. [ ] Review `DEPLOYMENT.md` troubleshooting section
3. [ ] View logs: `azd monitor --logs`
4. [ ] Check Azure Portal for resource status
5. [ ] Review Container App logs

## ✨ Success Criteria

Deployment is successful when:
- [ ] `azd up` completes without errors
- [ ] Application URL is provided
- [ ] Health endpoint returns 200 status
- [ ] Application loads in browser
- [ ] Container logs show no errors
- [ ] All Azure resources created in Portal

## 🎉 Post-Deployment Next Steps

After successful deployment:

1. **Configure AI Search** (if using QnA)
   - [ ] Upload documents/manuals
   - [ ] Create semantic search configuration
   - [ ] Test search queries

2. **Update Logic Apps**
   - [ ] Configure shipment workflow with your APIs
   - [ ] Configure conversation analysis action
   - [ ] Test Logic App endpoints

3. **Set Up Monitoring**
   - [ ] Create alert rules in Application Insights
   - [ ] Configure email notifications
   - [ ] Set up cost alerts

4. **Security Hardening**
   - [ ] Restrict SQL firewall
   - [ ] Enable Azure AD authentication
   - [ ] Configure custom domain
   - [ ] Enable HTTPS-only access

5. **Documentation**
   - [ ] Document environment variables
   - [ ] Create runbooks for common operations
   - [ ] Document disaster recovery procedures

---

## Ready? 

```bash
# When ready to deploy:
./setup-azure.sh
```

Once deployment is complete, proceed with **Post-Deployment Next Steps** above.

Questions? See `DEPLOYMENT.md` for comprehensive guidance.

Good luck! 🚀
