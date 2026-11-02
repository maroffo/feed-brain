# ABOUTME: Terraform settings for feed-brain: providers and the GCS state backend (own state, not BBP's).
# ABOUTME: Bootstrap the state bucket once with gcloud (see README runbook) before `terraform init`.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "gcs" {
    bucket = "playground-maroffo-feed-brain-tfstate"
    prefix = "feed-brain"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
