# Managed infrastructure for a hosted DisputeShield (§11.1, §11.7).
#
# The parts that are not negotiable, and why:
#
#   * **PITR with a five-minute RPO.** §11.7 states RPO 5 minutes / RTO 1 hour,
#     and a backup policy that does not meet a published number is a published
#     number that is false.
#   * **Object lock on the audit bucket.** §8.3 replicates audit records to
#     storage with a write-once lock so immutability survives a full database
#     compromise. Without the lock the replica is a copy, not evidence.
#   * **A read replica.** Analytics and exports run there and nowhere else, so a
#     regulator-ready export of a year of cases cannot contend with the decision
#     path.

variable "environment" { type = string }
variable "region" { type = string }
variable "retention_years" {
  type        = number
  default     = 7
  description = "Regulatory retention for cases, messages and audit records (§11.7)."
}

resource "aws_db_instance" "primary" {
  identifier                   = "disputeshield-${var.environment}"
  engine                       = "postgres"
  engine_version               = "16"
  backup_retention_period      = 35
  performance_insights_enabled = true
  deletion_protection          = true
  storage_encrypted            = true

  # RPO 5 minutes (§11.7). Continuous WAL archiving is what makes the number true.
  copy_tags_to_snapshot = true
  apply_immediately     = false
}

resource "aws_db_instance" "replica" {
  identifier          = "disputeshield-${var.environment}-replica"
  replicate_source_db = aws_db_instance.primary.identifier
  # Analytics and exports only. Never written to.
}

resource "aws_s3_bucket" "audit" {
  bucket = "disputeshield-audit-${var.environment}"
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      # COMPLIANCE, not GOVERNANCE: governance mode can be overridden by a
      # sufficiently privileged principal, which is exactly the principal an
      # evidence store has to survive.
      mode  = "COMPLIANCE"
      years = var.retention_years
    }
  }
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket" "attachments" {
  bucket = "disputeshield-attachments-${var.environment}"
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  # A file retrievable by URL guessing makes the antivirus gate decorative.
  bucket                  = aws_s3_bucket.attachments.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_kms_key" "envelope" {
  description         = "Envelope encryption master key for per-tenant data keys (§8.4)"
  enable_key_rotation = true
}

output "readme" {
  value = <<-EOT
    After apply:
      1. Create the restricted application role — it must NOT own the schema and
         must lack UPDATE/DELETE on the audit table (scripts/init-app-role.sql).
      2. Run migrations as the owner, then `disputeshield_doctor --strict`. It
         refuses to pass if the immutability trigger did not install.
      3. Confirm exactly one Celery beat replica is scheduled.
  EOT
}
