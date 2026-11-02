# ABOUTME: feed-brain on GCP: Cloud Run Job + daily Scheduler, DB/user on the shared Cloud SQL instance,
# ABOUTME: public site bucket, private data bucket (outbox, OPML), image repo, service accounts, IAM.

data "google_sql_database_instance" "shared" {
  name = var.sql_instance_name
}

data "google_vpc_access_connector" "shared" {
  name   = var.vpc_connector_name
  region = var.region
}

# --- Database on the shared instance ---

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_database" "feedbrain" {
  name     = "feedbrain"
  instance = data.google_sql_database_instance.shared.name
}

resource "google_sql_user" "feedbrain" {
  name     = "feedbrain"
  instance = data.google_sql_database_instance.shared.name
  password = random_password.db.result
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "feed-brain-db-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db.result
}

# --- Identities ---

resource "google_service_account" "job" {
  account_id   = "feed-brain-job"
  display_name = "feed-brain Cloud Run job"
}

resource "google_service_account" "scheduler" {
  account_id   = "feed-brain-scheduler"
  display_name = "feed-brain scheduler (runs the job)"
}

locals {
  # env var name => Secret Manager secret id
  secret_env = merge(
    {
      DB_PASSWORD    = google_secret_manager_secret.db_password.secret_id
      GEMINI_API_KEY = var.gemini_secret_id
      SES_USR        = var.ses_username_secret_id
      SES_PWD        = var.ses_password_secret_id
    },
    var.anthropic_secret_id == null ? {} : { ANTHROPIC_API_KEY = var.anthropic_secret_id },
    var.fetch_proxy_secret_id == null ? {} : { FETCH_PROXY = var.fetch_proxy_secret_id },
  )
  site_url = "https://storage.googleapis.com/${google_storage_bucket.site.name}"
}

resource "google_secret_manager_secret_iam_member" "job" {
  for_each  = local.secret_env
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.job.email}"
}

# --- Storage ---

resource "google_storage_bucket" "site" {
  name                        = "${var.project_id}-feed-brain-site"
  location                    = var.region
  uniform_bucket_level_access = true
  website {
    main_page_suffix = "index.html"
  }
}

resource "google_storage_bucket_iam_member" "site_public" {
  bucket = google_storage_bucket.site.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-feed-brain-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["vault-outbox/processed/"]
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "job_site" {
  bucket = google_storage_bucket.site.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_storage_bucket_iam_member" "job_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "feed-brain"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

# --- Job + schedule ---

resource "google_cloud_run_v2_job" "feed_brain" {
  name                = "feed-brain"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1
    template {
      service_account = google_service_account.job.email
      timeout         = "1800s"
      max_retries     = 1

      vpc_access {
        connector = data.google_vpc_access_connector.shared.id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = var.image
        args  = ["run"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
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
        env {
          name  = "SITE_BUCKET"
          value = google_storage_bucket.site.name
        }
        env {
          name  = "DATA_BUCKET"
          value = google_storage_bucket.data.name
        }
        env {
          name  = "SITE_URL"
          value = local.site_url
        }
        env {
          name  = "DIGEST_RECIPIENT"
          value = var.digest_recipient
        }
        env {
          name  = "SENDER_EMAIL"
          value = var.sender_email
        }

        dynamic "env" {
          for_each = local.secret_env
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
  }

  depends_on = [
    google_secret_manager_secret_iam_member.job,
    google_secret_manager_secret_version.db_password,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  name     = google_cloud_run_v2_job.feed_brain.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "daily" {
  name             = "feed-brain-daily"
  region           = var.region
  schedule         = var.schedule
  time_zone        = "Europe/Rome"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.feed_brain.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
