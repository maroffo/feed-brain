# ABOUTME: Inputs for the feed-brain stack; shared BehindBarsPulse resources are referenced by name only.
# ABOUTME: The recipient and image live in terraform.tfvars (gitignored); secrets are referenced, never valued.

variable "project_id" {
  description = "GCP project"
  type        = string
  default     = "playground-maroffo"
}

variable "region" {
  description = "Region of the job, buckets and scheduler (same as the shared Cloud SQL instance)"
  type        = string
  default     = "europe-west1"
}

variable "image" {
  description = "Container image for the job, e.g. europe-west1-docker.pkg.dev/<project>/feed-brain/feed-brain:<tag>"
  type        = string
}

variable "digest_recipient" {
  description = "Email address that receives the bulletin and the weekly recap"
  type        = string
}

variable "sender_email" {
  description = "From address (domain verified in BBP's SES)"
  type        = string
  default     = "digest@behindbars.news"
}

variable "schedule" {
  description = "Cron schedule of the daily run (Europe/Rome)"
  type        = string
  default     = "30 6 * * *"
}

variable "sql_instance_name" {
  description = "Shared Cloud SQL instance (owned by BehindBarsPulse)"
  type        = string
  default     = "behindbars-prod-db"
}

variable "vpc_connector_name" {
  description = "Shared Serverless VPC Access connector (owned by BehindBarsPulse)"
  type        = string
  default     = "behindbars-prod-connector"
}

variable "gemini_secret_id" {
  description = "Existing Secret Manager secret with the Gemini API key"
  type        = string
  default     = "behindbars-prod-gemini-api-key"
}

variable "ses_username_secret_id" {
  description = "Existing Secret Manager secret with the SES SMTP username"
  type        = string
  default     = "behindbars-prod-ses-username"
}

variable "ses_password_secret_id" {
  description = "Existing Secret Manager secret with the SES SMTP password"
  type        = string
  default     = "behindbars-prod-ses-password"
}

variable "anthropic_secret_id" {
  description = "Secret with the Anthropic API key (feed-brain-anthropic-api-key); null disables Opus deep analysis"
  type        = string
  default     = null
}

variable "fetch_proxy_secret_id" {
  description = "Secret holding FETCH_PROXY (e.g. socks5://user:pass@host:1080, NordVPN service credentials); null = no proxy fallback"
  type        = string
  default     = null
}

variable "aws_profile" {
  description = "Local AWS CLI profile for the account that owns BBP's SES (set in terraform.tfvars); null = default credential chain"
  type        = string
  default     = null
}

variable "inbox_aws_region" {
  description = "SES receiving region for the newsletter inbox; must not hold another active receipt rule set"
  type        = string
  default     = "eu-west-1"
}

variable "inbox_domain" {
  description = "Mail domain whose every address lands in the newsletter inbox"
  type        = string
  default     = "in.behindbars.news"
}

variable "dns_zone_name" {
  description = "Existing Cloud DNS zone (BBP) that holds the inbox MX record"
  type        = string
  default     = "bbnews"
}
