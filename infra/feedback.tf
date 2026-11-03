# ABOUTME: Vote service behind the email's 👍/👎 links: public Cloud Run service (the HMAC-signed links are the
# ABOUTME: auth), its own service account (DB password + link key only), and the link key shared with the job.

resource "random_password" "feedback_key" {
  length  = 43
  special = false
}

resource "google_secret_manager_secret" "feedback_key" {
  secret_id = "feed-brain-feedback-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "feedback_key" {
  secret      = google_secret_manager_secret.feedback_key.id
  secret_data = random_password.feedback_key.result
}

resource "google_service_account" "feedback" {
  account_id   = "feed-brain-feedback"
  display_name = "feed-brain vote service"
}

locals {
  feedback_secret_env = {
    DB_PASSWORD  = google_secret_manager_secret.db_password.secret_id
    FEEDBACK_KEY = google_secret_manager_secret.feedback_key.secret_id
  }
}

resource "google_secret_manager_secret_iam_member" "feedback" {
  for_each  = local.feedback_secret_env
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.feedback.email}"
}

resource "google_cloud_run_v2_service" "feedback" {
  name                = "feed-brain-feedback"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.feedback.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    vpc_access {
      connector = data.google_vpc_access_connector.shared.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image
      args  = ["serve"]

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "DB_HOST"
        value = data.google_sql_database_instance.shared.private_ip_address
      }
      env {
        name  = "DB_PORT"
        value = "5432"
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.feedbrain.name
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.feedbrain.name
      }

      dynamic "env" {
        for_each = local.feedback_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.feedback,
    google_secret_manager_secret_version.feedback_key,
  ]
}

# Public on purpose: the 👍/👎 links come from an email, so no Google identity is available.
# Requests without a valid HMAC signature get a 403 before any database read.
resource "google_cloud_run_v2_service_iam_member" "feedback_public" {
  name     = google_cloud_run_v2_service.feedback.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
