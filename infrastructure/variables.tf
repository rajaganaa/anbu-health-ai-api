variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
  default     = "7968d879-2622-46bd-b9c0-144cfcef2e92"
}

variable "resource_group_name" {
  description = "Existing resource group name"
  type        = string
  default     = "antahkarana-rg"
}

variable "location" {
  description = "Azure region for the resource group itself"
  type        = string
  default     = "eastus"
}

variable "resources_location" {
  description = "Azure region for resources inside the RG (workspace, environment, app, redis) — different from the RG's own region"
  type        = string
  default     = "centralindia"
}

variable "container_app_env_name" {
  description = "Existing Container App Environment name"
  type        = string
  default     = "antahkarana-env"
}

variable "log_analytics_workspace_name" {
  description = "Existing Log Analytics workspace name"
  type        = string
  default     = "workspace-antahkaranarg05vB"
}

variable "container_app_name" {
  description = "Container App name"
  type        = string
  default     = "anbu-health-ai"
}

variable "container_image" {
  description = "Backend container image (ghcr.io)"
  type        = string
  default     = "ghcr.io/rajaganaa/anbu-health-ai-api:latest"
}

variable "ghcr_username" {
  description = "GitHub Container Registry username (for pulling private images)"
  type        = string
  default     = "rajaganaa"
}

# ── Secrets (mark sensitive — pass via terraform.tfvars or TF_VAR_* env, never commit) ──
variable "groq_key" {
  type      = string
  sensitive = true
}
variable "vision_github_token" {
  type      = string
  sensitive = true
}
variable "wandb_key" {
  type      = string
  sensitive = true
}
variable "qdrant_url" {
  type      = string
  sensitive = true
}
variable "qdrant_key" {
  type      = string
  sensitive = true
}
variable "supabase_url" {
  type      = string
  sensitive = true
  default   = ""
}
variable "supabase_service_key" {
  type      = string
  sensitive = true
  default   = ""
}
variable "msg91_authkey" {
  type      = string
  sensitive = true
  default   = ""
}
variable "msg91_template_id" {
  type      = string
  sensitive = true
  default   = ""
}
# Added: these three were already live in the Azure portal (set by the CI
# workflow's `az containerapp update --set-env-vars`, or by hand for
# sarvam_key) but were missing from main.tf — meaning a `terraform apply`
# would have silently deleted Firebase auth and Sarvam TTS config from the
# running container. Declaring them here so Terraform's state matches
# reality. Defaults to "" so a plan/apply without these set doesn't fail;
# fill real values via terraform.tfvars or -var flags before applying.
variable "firebase_service_account_json" {
  type      = string
  sensitive = true
  default   = ""
}
variable "firebase_project_id" {
  type      = string
  sensitive = true
  default   = ""
}
variable "sarvam_api_key" {
  type      = string
  sensitive = true
  default   = ""
}
variable "ghcr_token" {
  description = "GitHub PAT with read:packages, for pulling the private container image"
  type        = string
  sensitive   = true
  default     = ""
}

# ── Scaling (tune these as real traffic data comes in from Stage 0 monitoring) ──
variable "min_replicas" {
  description = "Minimum running replicas. 0 = scale-to-zero (cheap, but cold starts hurt — ~10-20s to reload torch/sentence-transformers on every wake). 1 = always-warm (small fixed cost, no cold starts)."
  type        = number
  default     = 1
}
variable "max_replicas" {
  description = "Maximum replicas Container Apps can scale out to under load."
  type        = number
  default     = 5
}
variable "container_cpu" {
  description = "vCPU per replica. Azure Container Apps requires CPU/memory in fixed combinations (e.g. 0.5/1Gi, 1.0/2Gi, 2.0/4Gi)."
  type        = number
  default     = 1.0
}
variable "container_memory" {
  description = "Memory per replica — must pair validly with container_cpu (Azure-enforced ratio)."
  type        = string
  default     = "2Gi"
}
variable "http_concurrent_requests_per_replica" {
  description = "Target concurrent requests per replica before Container Apps spins up another one. Lower = scales out sooner (safer, costs more); higher = fewer replicas (cheaper, riskier under spiky load)."
  type        = number
  default     = 10
}
variable "upstash_redis_url" {
  description = "Upstash Redis connection string (rediss://default:<password>@<host>:<port>)"
  type        = string
  sensitive   = true
  default     = ""
}