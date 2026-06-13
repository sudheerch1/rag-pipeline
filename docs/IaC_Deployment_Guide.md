# RAG Pipeline — IaC Deployment Guide
## CloudFormation: Full Recreate Playbook

---

## What This Template Covers

| Resource | Automated by CFN? | Notes |
|---|---|---|
| S3 (3 buckets) | ✅ Yes | Including S3→SQS notification wiring |
| SQS (queue + DLQ) | ✅ Yes | 300s visibility, 3 retries, DLQ |
| DynamoDB | ✅ Yes | Table + GSI + encryption + PITR |
| Lambda (both functions) | ✅ Yes | arm64, env vars, X-Ray, OTEL layer |
| SQS → Lambda trigger | ✅ Yes | BatchSize=1, ReportBatchItemFailures |
| API Gateway | ✅ Yes | /query (auth+key), /health (public) |
| Cognito (pool + client) | ✅ Yes | Implicit grant, hosted UI |
| CloudFront | ✅ Yes | OAC, HTTPS redirect, SPA 404 handling |
| CloudWatch dashboard | ✅ Yes | All 5 widgets pre-built |
| CloudWatch alarms | ✅ Yes | DLQ depth + Lambda error rate |
| SNS (email alert) | ✅ Yes | Email subscription (confirm after deploy) |
| IAM role + policies | ✅ Yes | All permissions scoped correctly |
| Secrets Manager | ✅ Yes | OpenSearch credentials |
| Parameter Store | ✅ Yes | LangSmith key + OpenSearch endpoint |
| OpenSearch domain | ✅ Yes | Creates cluster |
| OpenSearch index mapping | ❌ Manual | `python create_index.py` (post-deploy) |
| OpenSearch hybrid pipeline | ❌ Manual | `python setup_hybrid_search.py` (post-deploy) |
| OpenSearch IAM role mapping | ❌ Manual | Dashboards UI (post-deploy) |
| Lambda deployment packages | ❌ Pre-step | Upload zips to S3 before stack deploy |
| Frontend build | ❌ Pre-step | Build + upload React app to S3 |

---

## Step-by-Step Recreate Process

### Pre-Step 1 — Build and Upload Lambda Packages

Do this BEFORE deploying the stack. The stack references these S3 keys.

```powershell
# Create a temporary bootstrap bucket (or use any existing bucket)
# Then build and upload both Lambda packages

# Query Lambda
Copy-Item *.py .\package\
Compress-Archive -Path .\package\* -DestinationPath query_lambda.zip -Force
aws s3 cp query_lambda.zip s3://rag-processed-<ACCOUNT_ID>/deployments/query_package.zip `
  --profile rag-dev --region us-east-1

# Ingestion Lambda
Compress-Archive -Path .\package\* -DestinationPath ingestion_lambda.zip -Force
aws s3 cp ingestion_lambda.zip s3://rag-processed-<ACCOUNT_ID>/deployments/ingestion_package.zip `
  --profile rag-dev --region us-east-1
```

> ⚠️ **Chicken-and-egg note:** The processed bucket is created by the stack, but Lambda needs packages in it at stack creation time. Resolution: create the processed bucket manually first (or use the `--capabilities CAPABILITY_NAMED_IAM` deploy and accept that Lambda will fail to create until you upload the package — CloudFormation will retry).
>
> **Cleanest approach:** Deploy the stack with `UpdateFunctionCode` disabled first (comment out Code: block, use inline placeholder), then upload packages and update function code separately.

### Step 1 — Deploy the Stack

```powershell
aws cloudformation deploy `
  --template-file rag-stack.yaml `
  --stack-name rag-pipeline `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    Environment=prod `
    LangSmithApiKey=ls__YOUR_KEY_HERE `
    OpenSearchMasterPassword=YOUR_SECURE_PASSWORD `
    NotificationEmail=your@email.com `
    OpenSearchInstanceType=t3.small.search `
  --profile rag-dev `
  --region us-east-1
```

Expected time: **20–25 minutes** (OpenSearch cluster creation dominates).

### Step 2 — Confirm SNS Email Subscription

Check your email. AWS sends a confirmation link for the alarm notification. Click it, or no alerts will fire.

### Step 3 — Get Stack Outputs

```powershell
aws cloudformation describe-stacks `
  --stack-name rag-pipeline `
  --query "Stacks[0].Outputs" `
  --profile rag-dev --region us-east-1
```

Copy these values:
- `OpenSearchDomainEndpoint` — needed for next step
- `LambdaRoleArn` — needed for OpenSearch Dashboards
- `ApiEndpoint` — update in frontend config
- `CognitoUserPoolId` + `CognitoAppClientId` — update in frontend config
- `ApiKeyId` — retrieve actual key value below

### Step 4 — Retrieve API Key Value

```powershell
aws apigateway get-api-key `
  --api-key <ApiKeyId from outputs> `
  --include-value `
  --profile rag-dev --region us-east-1
```

Update this in your frontend `config.ts`.

### Step 5 — Update OpenSearch Endpoint in Parameter Store

```powershell
aws ssm put-parameter `
  --name "/rag/opensearch/endpoint" `
  --value "<OpenSearchDomainEndpoint from stack outputs>" `
  --type String --overwrite `
  --profile rag-dev --region us-east-1
```

### Step 6 — OpenSearch Post-Deploy Configuration

These three steps cannot be automated by CloudFormation. They must run every time OpenSearch is recreated.

```powershell
# 1. Create the vector index with knn_vector mapping
python create_index.py

# 2. Create hybrid search pipeline (BM25 + KNN, 0.5/0.5 weights)
python setup_hybrid_search.py

# 3. Map IAM backend role in OpenSearch Dashboards UI:
#    → Open: https://<OpenSearchDomainEndpoint>/_dashboards
#    → Security → Roles → all_access → Mapped users tab → Backend roles
#    → Add: <LambdaRoleArn from stack outputs>
#    ⚠️ Use the IAM ROLE ARN — NOT the Lambda function ARN
```

### Step 7 — Upload Lambda Packages (if not done in pre-step)

```powershell
aws lambda update-function-code --function-name rag-query `
  --s3-bucket rag-processed-<ACCOUNT_ID> `
  --s3-key deployments/query_package.zip `
  --profile rag-dev --region us-east-1

aws lambda update-function-code --function-name rag-document-processor `
  --s3-bucket rag-processed-<ACCOUNT_ID> `
  --s3-key deployments/ingestion_package.zip `
  --profile rag-dev --region us-east-1
```

### Step 8 — Upload Documents

```powershell
python reset_document.py --file johnson_v._superior_court_1.pdf
python reset_document.py --file raceway_ford_cases.pdf

aws s3 cp "C:\...\johnson_v._superior_court_1.pdf" s3://rag-raw-<ACCOUNT_ID>/ --profile rag-dev
# Wait for ingestion to complete (watch logs), then upload second file
aws s3 cp "C:\...\raceway_ford_cases.pdf" s3://rag-raw-<ACCOUNT_ID>/ --profile rag-dev
```

### Step 9 — Upload Frontend

```powershell
# Build React app (update API endpoint and Cognito config in src/config.ts first)
npm run build
aws s3 sync ./build s3://rag-frontend-<ACCOUNT_ID>/ --profile rag-dev

# Invalidate CloudFront cache
aws cloudfront create-invalidation `
  --distribution-id <from stack outputs or console> `
  --paths "/*" --profile rag-dev
```

### Step 10 — Smoke Test

```powershell
# Health check
(Invoke-WebRequest -Uri "<ApiEndpoint>/health" -UseBasicParsing).Content
# Expected: {"status": "healthy", "opensearch": "reachable"}

# Query test
Invoke-WebRequest -Uri "<ApiEndpoint>/query" -Method POST `
  -Headers @{"x-api-key"="<ApiKeyValue>"; "Content-Type"="application/json"} `
  -Body '{"question":"What was Gregory Johnson convicted of?"}' -UseBasicParsing
```

---

## Teardown (Save Costs When Not In Use)

```powershell
# Option A: Delete just OpenSearch (saves $50-100/month, stack stays intact)
aws opensearch delete-domain --domain-name rag-vectors --profile rag-dev --region us-east-1

# Option B: Delete entire stack (saves everything — full recreate needed next time)
aws cloudformation delete-stack --stack-name rag-pipeline --profile rag-dev --region us-east-1
# ⚠️ S3 buckets must be empty before CFN can delete them:
aws s3 rm s3://rag-raw-<ACCOUNT_ID>/ --recursive --profile rag-dev
aws s3 rm s3://rag-processed-<ACCOUNT_ID>/ --recursive --profile rag-dev
aws s3 rm s3://rag-frontend-<ACCOUNT_ID>/ --recursive --profile rag-dev
```

---

## Why Some Things Can't Be Automated

| Gap | Why CFN Can't Do It | Workaround |
|---|---|---|
| OpenSearch index mapping (`knn_vector` type) | CFN OpenSearch resource doesn't expose index-level APIs | `create_index.py` via Python OpenSearch client |
| OpenSearch hybrid search pipeline | Not a CFN-supported property | `setup_hybrid_search.py` via REST API |
| OpenSearch backend role mapping | Internal OpenSearch security model, not AWS IAM | OpenSearch Dashboards UI (one-time click) |
| Lambda packages | Code must exist before Lambda can be created | Pre-upload to S3, or use two-pass deploy |

> **Production upgrade:** Use AWS CDK instead of raw CloudFormation. CDK Custom Resources can run the Python scripts as part of the deploy, closing all three gaps above. That's the full zero-manual-steps path.

---

## Variables to Update Per Deploy

When recreating, update these values in your source files:

| File | Variable | Where to Get Value |
|---|---|---|
| `src/config.ts` | `API_ENDPOINT` | Stack output `ApiEndpoint` |
| `src/config.ts` | `API_KEY` | `aws apigateway get-api-key --include-value` |
| `src/config.ts` | `USER_POOL_ID` | Stack output `CognitoUserPoolId` |
| `src/config.ts` | `CLIENT_ID` | Stack output `CognitoAppClientId` |
| Parameter Store | `/rag/opensearch/endpoint` | Stack output `OpenSearchDomainEndpoint` |
