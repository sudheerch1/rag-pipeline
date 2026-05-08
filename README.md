cd C:\Tech\PROD-Grade-POC\RAG\rag-pipeline

# Stage all changes
git add handler.py query_handler.py create_index.py setup_hybrid_search.py README.md

# Commit
git commit -m "feat: hybrid search, cosine similarity, duplicate detection, CORS fix

- Cosine similarity index (scores improved from 0.003 to 0.76+)
- Hybrid search: BM25 + KNN vector search combined
- Duplicate detection: SHA-256 hash + DynamoDB GSI
- CORS headers added to all Lambda responses
- OpenSearch setup scripts documented"

# Push
git push origin main