# Deployment Guide

Code Recap can deploy generated HTML reports to various providers for sharing with clients.

## Available Providers

| Provider | Description | Requirements |
|----------|-------------|--------------|
| `zip` | Creates a zip file for manual sharing | None |
| `s3` | Deploys to AWS S3 | AWS CLI |
| `cloudflare` | Deploys to Cloudflare Pages | Node.js, wrangler CLI |

## Basic Usage

```bash
# Deploy a specific client
code-recap deploy --client acme --provider zip
code-recap deploy --client acme --provider s3
code-recap deploy --client "Beta Inc" --provider cloudflare

# Deploy all clients
code-recap deploy --all --provider zip

# List available providers
code-recap deploy --list-providers
```

## Provider Configuration

Configure providers in your `config.yaml`:

```yaml
deploy:
  providers:
    zip:
      output_dir: "output/zips"

    s3:
      bucket: "my-reports-bucket"   # Or use S3_BUCKET env var
      region: "us-east-1"           # Or use AWS_REGION env var
      prefix: ""                    # Optional path prefix

    cloudflare:
      project_prefix: "reports"
      account_id: ""
      api_token: ""
      access_emails:
        - "admin@company.example"
```

---

## AWS S3 Provider

Deploys reports to an S3 bucket using the AWS CLI.

### Setup

1. **Install AWS CLI**:
   ```bash
   pip install awscli
   # or
   uv pip install awscli
   ```

2. **Configure credentials**:
   ```bash
   aws configure
   # Enter your AWS Access Key ID, Secret Access Key, and region
   ```

3. **Create an S3 bucket** (if needed):
   ```bash
   aws s3 mb s3://my-reports-bucket --region us-east-1
   ```

4. **Configure** in `config.yaml` or environment:
   ```yaml
   deploy:
     providers:
       s3:
         bucket: "my-reports-bucket"
         region: "us-east-1"
   ```

   Or via environment variables:
   ```bash
   export S3_BUCKET="my-reports-bucket"
   export AWS_REGION="us-east-1"
   ```

5. **Deploy**:
   ```bash
   code-recap deploy --client acme --provider s3
   ```

### S3 Static Website Hosting (Optional)

To make reports publicly accessible via a web URL:

1. Enable static website hosting on your bucket
2. Configure bucket policy for public read access (or use CloudFront)
3. Reports will be available at: `https://{bucket}.s3.{region}.amazonaws.com/{client}/index.html`

### S3 with CloudFront (Recommended for Production)

For better performance and custom domains:

1. Create a CloudFront distribution pointing to your S3 bucket
2. Configure your custom domain
3. Use Origin Access Control (OAC) to keep the bucket private

### Per-Client Custom URLs

Configure custom URLs (e.g., CloudFront domains) per client:

```yaml
html_report:
  clients:
    "Acme Corp":
      deploy:
        s3:
          url: "https://reports.acme.example/acme_corp/index.html"
```

This URL is displayed after deployment instead of the default S3 URL.

---

## Cloudflare Pages Provider

Deploys to Cloudflare Pages with optional Access control.

### Setup

1. **Install wrangler** (or use via npx):
   ```bash
   npm install -g wrangler
   ```

2. **Authenticate**:
   ```bash
   wrangler login
   ```

3. **Deploy**:
   ```bash
   code-recap deploy --client acme --provider cloudflare
   ```

The provider automatically creates Projects if they don't exist.

### Cloudflare Access (Optional)

To restrict report access to specific email addresses:

1. Create an API token at [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Grant permission: `Account > Access: Organizations, Identity Providers, and Groups > Edit`
3. Set the token:
   ```bash
   export CLOUDFLARE_API_TOKEN='your-token'
   # Or add to config.yaml under deploy.providers.cloudflare.api_token
   ```
4. Configure allowed emails in `config.yaml` (global and/or per-client)

### Per-Client Cloudflare Overrides

Configure client-specific Cloudflare settings in the `html_report.clients` section:

```yaml
html_report:
  clients:
    "Acme Corp":
      deploy:
        cloudflare:
          project_name: "acme-dev-reports"  # Full name (ignores prefix)
          access_emails:                     # Additional emails for this client
            - "cto@acme-corp.example"
            - "pm@acme-corp.example"
```

---

## Zip Provider

Creates timestamped zip files for manual distribution:

```bash
code-recap deploy --client acme --provider zip
# Creates: output/zips/Acme-Report-2025-01-15.zip
```

The zip contains all HTML files for that client's report.

---

## Custom Providers (Plugins)

You can install third-party providers or create your own. Plugins are automatically discovered when installed alongside Code Recap.

```bash
code-recap deploy --list-providers
# Output:
#   cloudflare
#   s3
#   zip
#   my-custom-provider (plugin)
```

See the [Extending Guide](extending.md) for how to create custom deployment providers.
