# AWS Setup for PR Preview Environments

One-time AWS provisioning for the Lambda PR preview workflow
(`.github/workflows/pr-preview.yml`). This creates the IAM roles, ECR
repository, and cost controls needed to spin up throwaway preview instances
per pull request via Lambda Function URLs.

## Prerequisites

- AWS CLI authenticated (`aws sts get-caller-identity` succeeds)
- GitHub CLI authenticated (`gh auth status` succeeds)

## Variables

Replace these throughout the commands below:

| Placeholder | Description |
|-------------|-------------|
| `<ACCOUNT_ID>` | Your AWS account ID |
| `<REGION>` | AWS region (e.g. `us-east-2`) |
| `<YOUR_EMAIL>` | Email for budget alerts |

## 1. Create the GitHub OIDC identity provider

Once per AWS account — allows GitHub Actions to assume IAM roles without
stored credentials:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

## 2. Create the Lambda execution role

Lambda assumes this role to run the preview function. It needs basic
execution permissions (CloudWatch Logs) and ECR image pull:

```bash
aws iam create-role --role-name fintrack-lambda-preview \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }]
  }'
aws iam attach-role-policy --role-name fintrack-lambda-preview \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

## 3. Create the deploy role

GitHub Actions assumes this role via OIDC. The trust policy `sub` condition
uses wildcards (`mrdefenestrator*/fintrack*`) because GitHub's OIDC tokens
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

Attach the permissions policy. ECR resources are scoped to the preview repo.
Lambda resources are scoped to the `fintrack-pr-*` function name prefix:

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
      { "Sid": "Lambda", "Effect": "Allow",
        "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:fintrack-pr-*",
        "Action": [
          "lambda:CreateFunction", "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:DeleteFunction", "lambda:GetFunction",
          "lambda:GetFunctionUrlConfig", "lambda:CreateFunctionUrlConfig",
          "lambda:DeleteFunctionUrlConfig",
          "lambda:AddPermission", "lambda:RemovePermission" ] },
      { "Sid": "PassLambdaRole", "Effect": "Allow",
        "Action": "iam:PassRole",
        "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-lambda-preview" }
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
unexpected costs:

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
| `LAMBDA_EXECUTION_ROLE_ARN` | ✅ | `arn:aws:iam::<ACCOUNT_ID>:role/fintrack-lambda-preview` |
| `ECR_REPOSITORY` | — | defaults to `fintrack-preview` |

Or via the CLI:

```bash
gh variable set AWS_REGION --body "<REGION>"
gh variable set AWS_DEPLOY_ROLE_ARN --body "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-pr-preview-deployer"
gh variable set LAMBDA_EXECUTION_ROLE_ARN --body "arn:aws:iam::<ACCOUNT_ID>:role/fintrack-lambda-preview"
```

## Teardown

To remove everything:

```bash
# Delete any Lambda preview functions
aws lambda list-functions --region <REGION> \
  --query "Functions[?starts_with(FunctionName, 'fintrack-pr-')].FunctionName" \
  --output text | tr '\t' '\n' | while read fn; do
    aws lambda delete-function --function-name "$fn" --region <REGION>
  done

# ECR repository (force-deletes all images)
aws ecr delete-repository --repository-name fintrack-preview \
  --region <REGION> --force

# IAM roles and policies
aws iam delete-role-policy --role-name fintrack-pr-preview-deployer \
  --policy-name fintrack-pr-preview
aws iam delete-role --role-name fintrack-pr-preview-deployer

aws iam detach-role-policy --role-name fintrack-lambda-preview \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name fintrack-lambda-preview

# Budget (optional — only if created in step 5)
aws budgets delete-budget --account-id <ACCOUNT_ID> --region us-east-1 \
  --budget-name fintrack-preview-monthly

# OIDC provider (only if no other workflows use it)
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com

# GitHub variables
gh variable delete AWS_REGION
gh variable delete AWS_DEPLOY_ROLE_ARN
gh variable delete LAMBDA_EXECUTION_ROLE_ARN
```
