# ─────────────────────────────────────────────────────────────────────────────
# Anbu Health AI — Azure Infrastructure (Terraform)
#
# This codifies the resources you already created manually via `az` CLI:
#   - Resource Group:            antahkarana-rg
#   - Container App Environment: antahkarana-env
#   - Container App:              anbu-health-ai
#
# To adopt existing resources instead of recreating them, run the
# `terraform import` commands in README.md BEFORE `terraform apply`.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    product = "anbu-health-ai"
    patent  = "202641043947"
    owner   = "rajaganaa"
  }
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.container_app_env_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_app_environment" "main" {
  name                       = var.container_app_env_name
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_container_app" "anbu_health_ai" {
  name                         = var.container_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                 = "Single"

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name   = "anbu-health-ai-api"
      image  = var.container_image
      cpu    = 0.5
      memory = "1Gi"

      # ── Plain env vars (hardcoded — never wiped by Terraform/CI) ────────────
      env {
        name  = "PORT"
        value = "8000"
      }
      env {
        name  = "GROQ_MODEL"
        value = "llama-3.3-70b-versatile"
      }
      env {
        name  = "GROQ_MODEL_FALLBACK"
        value = "llama3-8b-8192"
      }
      env {
        name  = "GROQ_MODEL_FALLBACK2"
        value = "mixtral-8x7b-32768"
      }
      env {
        name  = "WANDB_PROJECT"
        value = "anbu-health-ai"
      }
      env {
        name  = "WANDB_ENTITY"
        value = "rajaganaa-ai"
      }
      env {
        name  = "WANDB_MODE"
        value = "online"
      }
      env {
        name  = "MSG91_SENDER_ID"
        value = "ANBUHL"
      }
      env {
        name  = "MAX_PROMPTS_PER_DAY"
        value = "20"
      }

      # ── Secret-backed env vars ───────────────────────────────────────────────
      env {
        name        = "GROQ_API_KEY"
        secret_name = "groq-key"
      }
      env {
        name        = "VISION_GITHUB_TOKEN"
        secret_name = "vision-github-token"
      }
      env {
        name        = "WANDB_API_KEY"
        secret_name = "wandb-key"
      }
      env {
        name        = "QDRANT_URL"
        secret_name = "qdrant-url"
      }
      env {
        name        = "QDRANT_API_KEY"
        secret_name = "qdrant-key"
      }
      env {
        name        = "SUPABASE_URL"
        secret_name = "supabase-url"
      }
      env {
        name        = "SUPABASE_SERVICE_KEY"
        secret_name = "supabase-key"
      }
      env {
        name        = "MSG91_AUTH_KEY"
        secret_name = "msg91-authkey"
      }
      env {
        name        = "MSG91_TEMPLATE_ID"
        secret_name = "msg91-template-id"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport         = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  secret {
    name  = "groq-key"
    value = var.groq_key
  }
  secret {
    name  = "vision-github-token"
    value = var.vision_github_token
  }
  secret {
    name  = "wandb-key"
    value = var.wandb_key
  }
  secret {
    name  = "qdrant-url"
    value = var.qdrant_url
  }
  secret {
    name  = "qdrant-key"
    value = var.qdrant_key
  }
  secret {
    name  = "supabase-url"
    value = var.supabase_url
  }
  secret {
    name  = "supabase-key"
    value = var.supabase_service_key
  }
  secret {
    name  = "msg91-authkey"
    value = var.msg91_authkey
  }
  secret {
    name  = "msg91-template-id"
    value = var.msg91_template_id
  }

  dynamic "registry" {
    for_each = var.ghcr_token != "" ? [1] : []
    content {
      server               = "ghcr.io"
      username             = var.ghcr_username
      password_secret_name = "ghcr-token"
    }
  }

  dynamic "secret" {
    for_each = var.ghcr_token != "" ? [1] : []
    content {
      name  = "ghcr-token"
      value = var.ghcr_token
    }
  }

  tags = {
    product = "anbu-health-ai"
    patent  = "202641043947"
  }
}
