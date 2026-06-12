output "app_url" {
  description = "Live URL of the Anbu Health AI API"
  value       = "https://${azurerm_container_app.anbu_health_ai.ingress[0].fqdn}"
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "container_app_environment" {
  value = azurerm_container_app_environment.main.name
}

output "log_analytics_workspace_id" {
  value     = azurerm_log_analytics_workspace.main.id
  sensitive = true
}
