# ABOUTME: Values needed after apply: where the site lives, which buckets the Mac sync uses, the job name.
# ABOUTME: Put data_bucket in the Mac's .env as DATA_BUCKET for `feed-brain sync-vault`.

output "site_url" {
  value = "${local.site_url}/index.html"
}

output "site_bucket" {
  value = google_storage_bucket.site.name
}

output "data_bucket" {
  value = google_storage_bucket.data.name
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "job_name" {
  value = google_cloud_run_v2_job.feed_brain.name
}
