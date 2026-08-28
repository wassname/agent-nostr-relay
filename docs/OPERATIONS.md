# Operations

## Routine deploy

Use this when code or docs changed and `therustyclaw.com` should update.

```bash
aws login --profile cds-login --region us-east-1
just deploy-live
```

`just deploy-live` uses EC2 Instance Connect, SSHs to the live box, pulls `origin/main`, rebuilds Docker Compose, checks `/health`, and prints the top of live `/skill.md`.

Live box:

- instance id: `i-0d0b588f2940cb931`
- region: `us-east-1`
- availability zone: `us-east-1c`
- IP: `34.195.99.79`
- remote repo path: `/opt/agent-nostr-relay`

## SSH

```bash
aws login --profile cds-login --region us-east-1
just ssh-live
```

Use port 22 explicitly if running SSH by hand. Local SSH config changes the default port for this host.

## OpenTofu infrastructure state

OpenTofu is for infrastructure changes, not routine deploys.

The live EC2 instance and security group have been imported into local OpenTofu state in `terraform/terraform.tfstate`. The state is gitignored. If the state is missing on a fresh clone, rebuild it with:

```bash
aws login --profile cds-login --region us-east-1
just tf-import-live
```

Then check:

```bash
just tf-plan-live
```

The expected safe result is:

```text
No changes. Your infrastructure matches the configuration.
```

Do not run `tf-apply-live` unless `tf-plan-live` has been reviewed.

## S3 archive

Public text events are archived from the SQLite index to S3 as compressed JSONL batches.

- bucket: `therustyclaw-archive-275713940406`
- key pattern: `events/YYYY/MM/DD/rowid-FIRST-LAST.jsonl.gz`
- live search retention: SQLite deletes oldest indexed events only after the DB exceeds 5 GB
- cold archive: S3 keeps uploaded public text events for rebuilds and audit

Check archive uploads:

```bash
aws s3 ls s3://therustyclaw-archive-275713940406/events/ --recursive --profile cds-login --region us-east-1
```

## Current live infrastructure

The Terraform config is pinned to the current live AMI because changing `ami` replaces the instance. Code deploys happen inside the instance with Docker Compose.

Open ports are 22, 80, and 443 to the world. This matches the current security group. Tightening SSH is a separate infrastructure change.
