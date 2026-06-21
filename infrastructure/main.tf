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
# Azure Redis Cache — Anbu Health AI
# Add this to infrastructure/main.tf
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
  name                = var.log_analytics_workspace_name
  location            = var.resources_location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_app_environment" "main" {
  name                       = var.container_app_env_name
  location                   = var.resources_location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  lifecycle {
    ignore_changes = [log_analytics_workspace_id]
  }
}








resource "azurerm_container_app" "anbu_health_ai" {
  name                         = var.container_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                 = "Single"

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    # ── Autoscale on concurrent HTTP requests per replica ───────────────────
    # Without this rule, Container Apps only ever runs min_replicas..max_replicas
    # based on its own default (often too slow/blunt for LLM-call-shaped traffic,
    # where each request holds the connection open for several seconds while
    # waiting on Groq/Vision). This rule scales out proactively under real load.
    http_scale_rule {
      name                = "http-concurrency"
      concurrent_requests = var.http_concurrent_requests_per_replica
    }

    container {
      name   = "anbu-health-ai-api"
      image  = var.container_image
      cpu    = var.container_cpu
      memory = var.container_memory

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
        name        = "GROQ_API_KEYS_EXTRA"
        secret_name = "groq-keys-extra"
      }
      env {
        name        = "VISION_GITHUB_TOKEN"
        secret_name = "vision-github-token"
      }
      env {
        name        = "GEMINI_API_KEY"
        secret_name = "gemini-api-key"
      }
      env {
        name  = "GEMINI_VISION_MODEL"
        value = "gemini-1.5-pro"
      }


      env {
        name        = "GEMINI_SERVICE_ACCOUNT_JSON"
        secret_name = "gemini-vision-sa-json"
      }


      env {
        name  = "GEMINI_PROJECT_ID"
        value = var.gemini_project_id
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
      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }
      env {
        name        = "FIREBASE_SERVICE_ACCOUNT_JSON"
        secret_name = "firebase-service-account-json"
      }
      env {
        name        = "FIREBASE_PROJECT_ID"
        secret_name = "react-app-firebase-project-id"
      }
      env {
        name        = "SARVAM_API_KEY"
        secret_name = "sarvam-api-key"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }

      readiness_probe {
        transport               = "HTTP"
        path                    = "/ready"
        port                    = 8000

        interval_seconds        = 5
        timeout                 = 5
        failure_count_threshold = 10  # allow ~50s for torch/sentence-transformers/Qdrant to load
        success_count_threshold = 1
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
    name  = "groq-keys-extra"
    value = var.groq_keys_extra
  }
  secret {
    name  = "vision-github-token"
    value = var.vision_github_token
  }
  secret {
    name  = "gemini-api-key"
    value = var.gemini_api_key
  }
  secret {
  name  = "gemini-vision-sa-json"
  value = var.gemini_vision_sa_json
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
  # Added: these were live in Azure (via CI's `az containerapp update`, plus
  # a manual portal edit for sarvam-api-key) but absent here — see note in
  # variables.tf. Declaring them so `terraform apply` doesn't strip them.
  secret {
    name  = "firebase-service-account-json"
    value = var.firebase_service_account_json
  }
  secret {
    name  = "react-app-firebase-project-id"
    value = var.firebase_project_id
  }
  secret {
    name  = "sarvam-api-key"
    value = var.sarvam_api_key
  }
  secret {
    # Wires the Redis cache created above (azurerm_redis_cache.main) into the
    # app — previously this only existed as a Terraform *output*, never
    # actually passed to the running container, so a fresh `terraform apply`
    # would silently leave REDIS_URL unset (OTP falls back to in-memory,
    # which breaks across multiple replicas/restarts).
    name  = "redis-url"
    value = var.upstash_redis_url
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


