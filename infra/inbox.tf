# ABOUTME: Newsletter inbox: SES receives mail for var.inbox_domain into a private S3 bucket.
# ABOUTME: The job reads it keylessly: Google ID token of its service account -> AWS role via web identity.

data "aws_caller_identity" "current" {}

data "google_dns_managed_zone" "shared" {
  name = var.dns_zone_name
}

locals {
  inbox_prefix    = "mail/"
  inbox_rule_set  = "feed-brain"
  inbox_rule      = "feed-brain-inbox"
  inbox_audience  = "feed-brain-inbox"
  inbox_rule_arn  = "arn:aws:ses:${var.inbox_aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/${local.inbox_rule_set}:receipt-rule/${local.inbox_rule}"
  inbox_role_name = "feed-brain-inbox-reader"
}

# --- Storage ---

resource "aws_s3_bucket" "inbox" {
  bucket = "feed-brain-inbox-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "inbox" {
  bucket                  = aws_s3_bucket.inbox.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "inbox" {
  bucket = aws_s3_bucket.inbox.id

  rule {
    id     = "expire-unprocessed-mail"
    status = "Enabled"
    filter {
      prefix = local.inbox_prefix
    }
    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "inbox" {
  bucket = aws_s3_bucket.inbox.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SESWritesInboundMail"
      Effect    = "Allow"
      Principal = { Service = "ses.amazonaws.com" }
      Action    = "s3:PutObject"
      Resource  = "${aws_s3_bucket.inbox.arn}/${local.inbox_prefix}*"
      Condition = {
        StringEquals = { "AWS:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "AWS:SourceArn" = local.inbox_rule_arn }
      }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.inbox]
}

# --- SES receiving (own rule set, active only in var.inbox_aws_region) ---

resource "aws_ses_receipt_rule_set" "inbox" {
  rule_set_name = local.inbox_rule_set
}

resource "aws_ses_receipt_rule" "inbox" {
  name          = local.inbox_rule
  rule_set_name = aws_ses_receipt_rule_set.inbox.rule_set_name
  recipients    = [var.inbox_domain]
  enabled       = true
  scan_enabled  = true
  tls_policy    = "Require"

  s3_action {
    bucket_name       = aws_s3_bucket.inbox.id
    object_key_prefix = local.inbox_prefix
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.inbox]
}

resource "aws_ses_active_receipt_rule_set" "inbox" {
  rule_set_name = aws_ses_receipt_rule_set.inbox.rule_set_name
  depends_on    = [aws_ses_receipt_rule.inbox]
}

resource "google_dns_record_set" "inbox_mx" {
  managed_zone = data.google_dns_managed_zone.shared.name
  name         = "${var.inbox_domain}."
  type         = "MX"
  ttl          = 300
  rrdatas      = ["10 inbound-smtp.${var.inbox_aws_region}.amazonaws.com."]
}

# --- Keyless access for the job: Google-signed ID token -> sts:AssumeRoleWithWebIdentity ---
# For a service account ID token, AWS maps accounts.google.com:aud to the token's azp (the SA
# unique id) and accounts.google.com:oaud to its aud (the audience the job requests).

resource "aws_iam_role" "inbox_reader" {
  name                 = local.inbox_role_name
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "accounts.google.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "accounts.google.com:aud"  = google_service_account.job.unique_id
          "accounts.google.com:oaud" = local.inbox_audience
          "accounts.google.com:sub"  = google_service_account.job.unique_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "inbox_reader" {
  name = "read-and-delete-inbox-mail"
  role = aws_iam_role.inbox_reader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.inbox.arn
        Condition = { StringLike = { "s3:prefix" = ["${local.inbox_prefix}*"] } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.inbox.arn}/${local.inbox_prefix}*"
      },
    ]
  })
}
