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
  description = "Azure region"
  type        = string
  default     = "centralindia"
}

variable "container_app_env_name" {
  description = "Existing Container App Environment name"
  type        = string
  default     = "antahkarana-env"
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
variable "ghcr_token" {
  description = "GitHub PAT with read:packages, for pulling the private container image"
  type        = string
  sensitive   = true
  default     = ""
}
