import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Remote Test MCP Server")

EXPECTED_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "secret_token_abc")

# In-memory mock database
CRM_CONTACTS: dict[str, dict[str, Any]] = {
    "user_a1b2": {"id": "user_a1b2", "email": "test@example.com", "name": "Test User"},
}
CRM_NOTES: list[dict[str, Any]] = []

KB_DOCS: list[dict[str, Any]] = [
    {
        "id": "doc_101",
        "doc_ref": "doc_101",
        "title": "Studio Hours",
        "content": "Open Tuesday through Saturday from 10:00 AM to 8:00 PM WAT. Closed Sundays and Mondays.",
    },
    {
        "id": "doc_102",
        "doc_ref": "doc_102",
        "title": "Deposit Policy",
        "content": "A 20% deposit is required for all appointments to secure your slot. Deposits are applied toward the final price.",
    },
    {
        "id": "doc_103",
        "doc_ref": "doc_103",
        "title": "Piercing Aftercare",
        "content": "Clean with sterile saline spray 2-3 times daily. Do not twist jewelry and avoid sleeping on unhealed piercings.",
    },
]


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def verify_token(authorization: str | None = Header(None)) -> None:
    if not EXPECTED_AUTH_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if parts[1] != EXPECTED_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "remote-test-mcp"}


@app.post("/mcp")
async def handle_mcp(
    request: JsonRpcRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    verify_token(authorization)

    method = request.method
    params = request.params or {}

    # 1. Tool Discovery: tools/list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "result": {
                "tools": [
                    {
                        "name": "crm_contact_search",
                        "description": "Search CRM contacts by email or phone",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "email": {"type": "string"},
                                "phone": {"type": "string"},
                            },
                        },
                    },
                    {
                        "name": "crm_contact_upsert",
                        "description": "Create or update CRM contact and return contact_id",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "email": {"type": "string"},
                                "name": {"type": "string"},
                            },
                            "required": ["email", "name"],
                        },
                    },
                    {
                        "name": "crm_note_create",
                        "description": "Add a note or inquiry to a CRM contact record",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "attendee_ref": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["note"],
                        },
                    },
                    {
                        "name": "kb_search",
                        "description": "Search internal knowledge base documents",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "kb_document_get",
                        "description": "Fetch complete knowledge base document content by doc_ref or id",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "doc_ref": {"type": "string"},
                                "id": {"type": "string"},
                            },
                        },
                    },
                ]
            },
        }

    # 2. Tool Invocation: tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "crm_contact_search":
            email = arguments.get("email")
            match = next((c for c in CRM_CONTACTS.values() if c.get("email") == email), None)
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"contact": match} if match else {"contact": None},
            }

        if tool_name == "crm_contact_upsert":
            email = arguments.get("email", "")
            name = arguments.get("name", "")
            cid = f"rem_{abs(hash(email)) % 10000}"
            CRM_CONTACTS[cid] = {"id": cid, "email": email, "name": name}
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"contact_id": cid, "status": "upserted"},
            }

        if tool_name == "crm_note_create":
            attendee_ref = arguments.get("attendee_ref")
            note_content = arguments.get("note", "")
            note_record = {
                "note_id": f"note_{len(CRM_NOTES) + 1}",
                "attendee_ref": attendee_ref,
                "note": note_content,
            }
            CRM_NOTES.append(note_record)
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"note_id": note_record["note_id"], "status": "created"},
            }

        if tool_name == "kb_search":
            raw_query = (arguments.get("query") or "").lower()
            stop_words = {
                "iron", "ink", "tattoo", "piercing", "studio",
                "the", "a", "an", "is", "for", "and", "what", "are", "your", "do", "you", "have"
            }
            tokens = [
                word.strip("?,!.:'\"")
                for word in raw_query.split()
                if word.strip("?,!.:'\"") and word.strip("?,!.:'\"") not in stop_words
            ]

            def score_doc(doc: dict[str, Any]) -> int:
                title = doc.get("title", "").lower()
                content = doc.get("content", "").lower()
                if not tokens:
                    return 1 if raw_query in title or raw_query in content else 0
                return sum(1 for t in tokens if t in title or t in content)

            scored = [(score_doc(d), d) for d in KB_DOCS]
            matches = [d for score, d in scored if score > 0]
            matches.sort(key=lambda x: score_doc(x), reverse=True)

            first_match = matches[0] if matches else None

            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    "documents": matches,
                    "doc_refs": [d["id"] for d in matches],
                    "doc_ref": first_match["id"] if first_match else None,
                    "answer": first_match["content"] if first_match else "No relevant studio policy found.",
                    "content": first_match["content"] if first_match else "",
                    "title": first_match["title"] if first_match else "",
                },
            }

        if tool_name == "kb_document_get":
            target_ref = arguments.get("doc_ref") or arguments.get("id")
            doc = next(
                (d for d in KB_DOCS if d.get("id") == target_ref or d.get("doc_ref") == target_ref),
                None,
            )
            if doc:
                return {
                    "jsonrpc": "2.0",
                    "id": request.id,
                    "result": {
                        "document": doc,
                        "content": doc["content"],
                        "title": doc["title"],
                        "doc_ref": doc["id"],
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {"doc_ref": target_ref, "content": ""},
            }

        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
        }

    return {
        "jsonrpc": "2.0",
        "id": request.id,
        "error": {"code": -32601, "message": f"Method '{method}' not recognized"},
    }