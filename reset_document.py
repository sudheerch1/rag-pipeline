"""
reset_document.py
-----------------
Deletes a document's metadata from DynamoDB so it can be reprocessed.

Use this when:
  - OpenSearch index was recreated (old embeddings gone, need fresh ones)
  - Document content was updated and you want to force reprocessing
  - Testing ingestion pipeline changes

Usage:
    python reset_document.py                                    # resets default doc
    python reset_document.py my_document.pdf                   # resets specific doc
    python reset_document.py --list                            # lists all documents

Examples:
    python reset_document.py johnson_v._superior_court_1.pdf
    python reset_document.py --all                             # resets ALL documents
"""

import sys
import boto3

PROFILE_NAME   = 'rag-dev'
METADATA_TABLE = 'rag-metadata'
DEFAULT_DOC    = 'johnson_v._superior_court_1.pdf'


def get_table():
    return boto3.Session(profile_name=PROFILE_NAME).resource('dynamodb').Table(METADATA_TABLE)


def list_documents():
    """List all documents currently in DynamoDB metadata table."""
    table = get_table()
    response = table.scan(ProjectionExpression='document_id, #s, #ts', 
                          ExpressionAttributeNames={'#s': 'status', '#ts': 'timestamp'})
    items = response.get('Items', [])
    if not items:
        print("No documents found in metadata table.")
        return
    print(f"\nDocuments in {METADATA_TABLE}:")
    print("-" * 60)
    for item in items:
        print(f"  {item['document_id']}")
        print(f"    status:    {item.get('status', 'unknown')}")
        print(f"    timestamp: {item.get('timestamp', 'unknown')}")
    print(f"\nTotal: {len(items)} document(s)")


def reset_document(document_id: str):
    """Delete document metadata so it can be reprocessed."""
    table = get_table()
    table.delete_item(Key={'document_id': document_id})
    print(f"✅ Reset: '{document_id}' deleted from DynamoDB")
    print(f"   Next upload of this file will trigger full reprocessing")


def reset_all_documents():
    """Delete ALL document metadata — use with caution!"""
    table = get_table()
    response = table.scan(ProjectionExpression='document_id')
    items = response.get('Items', [])

    if not items:
        print("No documents to reset.")
        return

    confirm = input(f"⚠️  This will reset {len(items)} documents. Type 'yes' to confirm: ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return

    for item in items:
        table.delete_item(Key={'document_id': item['document_id']})
        print(f"  Deleted: {item['document_id']}")

    print(f"\n✅ Reset {len(items)} documents from DynamoDB")


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--list' in args:
        list_documents()
    elif '--all' in args:
        reset_all_documents()
    elif args:
        reset_document(args[0])
    else:
        reset_document(DEFAULT_DOC)