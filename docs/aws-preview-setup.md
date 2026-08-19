# AWS Setup for PR Preview Environments

One-time AWS provisioning for the App Runner PR preview workflow
(`.github/workflows/pr-preview.yml`). This creates the IAM roles, ECR
repository, and cost controls needed to spin up throwaway preview instances
per pull request.

## Prerequisites

- AWS CLI authenticated (`aws sts get-caller-identity` succeeds)
- GitHub CLI authenticated (`gh auth status` succeeds)

## Variables

Replace these throughout the commands below:

| Placeholder | Description |
|-------------|-------------|
| `<ACCOUNT_ID>` | Your AWS account ID |
| `<REGION>` | [App Runner region](https://docs.aws.amazon.com/general/latest/gr/apprunner.html) (e.g. `us-east-2`) |
| `<YOUR_EMAIL>` | Email for budget alerts |

## 1. Create the GitHub OIDC identity provider

Once per AWS account — allows GitHub Actions to assume IAM roles without
stored credentials:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

## 2. Create the App Runner → ECR access role

App Runner assumes this role to pull container images from ECR:

```bash
aws iam create-role --role-name fintrack-apprunner-ecr-access \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Service": "build.apprunner.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }]
  }'
aws iam attach-role-policy --role-name fintrack-apprunner-ecr-access \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

## 3. Create the deploy role

GitHub Actions assumes this role via OIDC. The trust policy `sub` condition
uses wildcards (`mrdefenestrator*/fintrack*`) because GitHub's OIDC tokens now
append numeric IDs to the owner and repo names (e.g.
`repo:mrdefenestrator@12345/fintrack@67890:pull_request`):

```bash
aws iam create-role --role-name fintrack-pr-preview-deployer \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
        "StringLike": { "token.actions.githubusercontent.com:sub": "repo:mrdefenestrator*/fintrack*:*" }
      }
    }]
  }'
```

Attach the permissions policy. ECR and App Runner resources are scoped to the
preview repo name and `fintrack-pr-*` service prefix. `ListServices` and
`GetAuthorizationToken` require `"Resource": "*"` — they don't support
resource-level restrictions:

```bash
aws iam put-role-policy --role-name fintrack-pr-preview-deployer \
  --policy-name fintrack-pr-preview --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      { "Sid": "ECRAuth", "Effect": "Allow",
        "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
      { "Sid": "ECR", "Effect": "Allow",
        "Resource": "arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/fintrack-preview",
        "Action": [
          "ecr:CreateRepository", "ecr:DescribeRepositories",
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:ListImages", "ecr:BatchDeleteImage" ] },
      { "Sid": "AppRunnerList", "Effect": "Allow",
        "Action": "apprunner:ListServices", "Resource": "*" },
      { "Sid": "AppRunner", "Effect": "Allow",
        "Resource": "arn:aws:apprunner:<REGION>:<ACCOUNT_ID>:service/fintrack-pr-*",
        "Action": [
          "apprunner:CreateService", "apprunner:UpdateService",
          "apprunner:DeleteService", "apprunner:DescribeService" ] },
      { "Sid": "AppRunnerSLR", "Effect": "Allow",
        "Action": "iam:CreateServiceLinkedRole", "Resource": "*",
        "Condition": { "StringEquals": {
          "iam:AWSServiceName": "apprunner.amazonaws.com" } } },
      { "Sid": "PassAccessRole", "Effect": "Allow",
        "Action": "iam:PassRole",
        "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-apprunner-ecr-access" }
    ]
  }'
```

## 4. Create the ECR repository and lifecycle policy

The lifecycle policy auto-expires untagged images after 1 day and keeps at
most 10 tagged images, preventing unbounded storage growth:

```bash
aws ecr create-repository --repository-name fintrack-preview --region <REGION>

aws ecr put-lifecycle-policy --repository-name fintrack-preview \
  --region <REGION> --lifecycle-policy-text '{
    "rules": [
      { "rulePriority": 1, "description": "Expire untagged images after 1 day",
        "selection": { "tagStatus": "untagged", "countType": "sinceImagePushed",
          "countUnit": "days", "countNumber": 1 },
        "action": { "type": "expire" } },
      { "rulePriority": 2, "description": "Keep only last 10 tagged images",
        "selection": { "tagStatus": "tagged", "tagPatternList": ["*"],
          "countType": "imageCountMoreThan", "countNumber": 10 },
        "action": { "type": "expire" } }
    ]
  }'
```

## 5. Budget alarm (optional)

Get emailed at 80% of a $5/month threshold — well above normal usage, catches
forgotten previews:

```bash
aws budgets create-budget --account-id <ACCOUNT_ID> --region us-east-1 \
  --budget '{
    "BudgetName": "fintrack-preview-monthly",
    "BudgetLimit": { "Amount": "5", "Unit": "USD" },
    "TimeUnit": "MONTHLY", "BudgetType": "COST"
  }' --notifications-with-subscribers '[{
    "Notification": { "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80, "ThresholdType": "PERCENTAGE" },
    "Subscribers": [{ "SubscriptionType": "EMAIL",
      "Address": "<YOUR_EMAIL>" }]
  }]'
```

> **Note:** The Budgets API endpoint is always `us-east-1` regardless of where
> your resources live.

## 6. Set the GitHub repository variables

At **Settings → Secrets and variables → Actions → Variables** (these are
variables, not secrets — none are sensitive):

| Variable | Required | Example / default |
|----------|----------|-------------------|
| `AWS_REGION` | ✅ | `us-east-2` |
| `AWS_DEPLOY_ROLE_ARN` | ✅ | `arn:aws:iam::<ACCOUNT_ID>:role/fintrack-pr-preview-deployer` |
| `APPRUNNER_ACCESS_ROLE_ARN` | ✅ | `arn:aws:iam::<ACCOUNT_ID>:role/fintrack-apprunner-ecr-access` |
| `ECR_REPOSITORY` | — | defaults to `fintrack-preview` |
| `APPRUNNER_CPU` | — | defaults to `1024` (1 vCPU) |
| `APPRUNNER_MEMORY` | — | defaults to `2048` (2 GB) |

Or via the CLI:

```bash
gh variable set AWS_REGION --body "<REGION>"
gh variable set AWS_DEPLOY_ROLE_ARN --body "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-pr-preview-deployer"
gh variable set APPRUNNER_ACCESS_ROLE_ARN --body "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-apprunner-ecr-access"
```

## Teardown

To remove everything:

```bash
# Delete any running App Runner services first
aws apprunner list-services --region <REGION> \
  --query "ServiceSummaryList[?starts_with(ServiceName, 'fintrack-pr-')].ServiceArn" \
  --output text | tr '\t' '\n' | while read arn; do
    aws apprunner delete-service --service-arn "$arn" --region <REGION>
  done

# ECR repository (force-deletes all images)
aws ecr delete-repository --repository-name fintrack-preview \
  --region <REGION> --force

# IAM roles and policies
aws iam delete-role-policy --role-name fintrack-pr-preview-deployer \
  --policy-name fintrack-pr-preview
aws iam delete-role --role-name fintrack-pr-preview-deployer

aws iam detach-role-policy --role-name fintrack-apprunner-ecr-access \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
aws iam delete-role --role-name fintrack-apprunner-ecr-access

# Budget (optional — only if created in step 5)
aws budgets delete-budget --account-id <ACCOUNT_ID> --region us-east-1 \
  --budget-name fintrack-preview-monthly

# OIDC provider (only if no other workflows use it)
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com

# GitHub variables
gh variable delete AWS_REGION
gh variable delete AWS_DEPLOY_ROLE_ARN
gh variable delete APPRUNNER_ACCESS_ROLE_ARN
```
