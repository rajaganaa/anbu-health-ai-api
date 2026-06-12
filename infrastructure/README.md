# Infrastructure as Code — Anbu Health AI (Terraform)

Codifies the Azure Container Apps setup that currently runs
`anbu-health-ai` in resource group `antahkarana-rg`.

## 1. Setup

```bash
cd infrastructure
terraform init
```

Create `terraform.tfvars` (DO NOT COMMIT — already in `.gitignore`):

```hcl
groq_key             = "gsk_..."
vision_github_token  = "ghp_..."
wandb_key            = "wandb_..."
qdrant_url           = "https://xxxx.aws.cloud.qdrant.io:6333"
qdrant_key           = "..."
supabase_url         = "https://xxxx.supabase.co"
supabase_service_key = "..."
msg91_authkey        = "..."
msg91_template_id    = "..."
ghcr_token           = "ghp_..."   # only if ghcr.io/rajaganaa/... is a private package
```

## 2. Adopt your EXISTING resources (don't recreate them!)

Since `antahkarana-rg`, `antahkarana-env`, and `anbu-health-ai` already
exist, import them into Terraform's state first:

```bash
# Resource group
terraform import azurerm_resource_group.main \
  /subscriptions/7968d879-2622-46bd-b9c0-144cfcef2e92/resourceGroups/antahkarana-rg

# Container App Environment
terraform import azurerm_container_app_environment.main \
  /subscriptions/7968d879-2622-46bd-b9c0-144cfcef2e92/resourceGroups/antahkarana-rg/providers/Microsoft.App/managedEnvironments/antahkarana-env

# Container App
terraform import azurerm_container_app.anbu_health_ai \
  /subscriptions/7968d879-2622-46bd-b9c0-144cfcef2e92/resourceGroups/antahkarana-rg/providers/Microsoft.App/containerApps/anbu-health-ai

# Log Analytics workspace (find its name first)
az monitor log-analytics workspace list -g antahkarana-rg -o table
terraform import azurerm_log_analytics_workspace.main \
  /subscriptions/7968d879-2622-46bd-b9c0-144cfcef2e92/resourceGroups/antahkarana-rg/providers/Microsoft.OperationalInsights/workspaces/<WORKSPACE_NAME>
```

## 3. Review plan (should show near-zero changes if import matched reality)

```bash
terraform plan
```

If the plan wants to *replace* resources, fix the `.tf` config to match
the real values first (e.g. log analytics workspace name) — never let
Terraform delete your live app.

## 4. Apply

```bash
terraform apply
```

## 5. Create the new secrets once (Supabase / MSG91)

Before the next CI/CD deploy, create the new Azure secrets referenced in
`.github/workflows/deploy.yml` (or `terraform apply` above will create
them for you if `terraform.tfvars` is filled in):

```bash
az containerapp secret set \
  --name anbu-health-ai --resource-group antahkarana-rg \
  --secrets \
    "supabase-url=https://xxxx.supabase.co" \
    "supabase-key=YOUR_SERVICE_ROLE_KEY" \
    "msg91-authkey=YOUR_MSG91_AUTHKEY" \
    "msg91-template-id=YOUR_TEMPLATE_ID"
```

> If you skip this, the app still works — `auth/otp.py` and
> `db/supabase_client.py` both degrade gracefully (dev-mode OTP,
> localStorage-only prompt counter) when these secrets are absent.

## What this gives you for your resume

- Infrastructure as Code (Terraform + AzureRM provider)
- Reproducible environment — `terraform apply` rebuilds the entire
  backend infra from scratch in a new subscription
- Secrets management via Azure Container Apps secrets (never in code)
- Clear separation: CI/CD (GitHub Actions) deploys the *application*,
  Terraform manages the *infrastructure*
