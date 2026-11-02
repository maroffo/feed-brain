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
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
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

# Newsletter inbox (SES receiving + S3). Its own region: SES allows one active receipt rule set
# per region and us-east-1's belongs to other workloads in the same account.
provider "aws" {
  region  = var.inbox_aws_region
  profile = var.aws_profile
}
