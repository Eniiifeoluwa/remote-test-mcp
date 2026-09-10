import json
import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Remote Test MCP Server")

EXPECTED_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "secret_token_abc")

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
                                "phone_e164": {"type": "string"},
                            },
                            "required": ["email", "name"],
                        },
                    },
                    {
                        "name": "crm_note_create",
                        "description": "Add a note to a CRM contact record",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                # Canonical field names — this is what the action-agent
                                # platform actually sends (see app/core/sagas.py:_crm_note_args
                                # and app/core/planner.py's HeuristicPlanner chaining).
                                "contact_ref": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["contact_ref", "body"],
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
            # Expose contact_id at the TOP LEVEL of the result, not just nested in
            # "contact". app/core/planner.py's HeuristicPlanner does:
            #   if "contact_id" in outcome and "contact_ref" not in args:
            #       args["contact_ref"] = outcome["contact_id"]
            # so if this step's result has no top-level contact_id, that auto-chaining
            # silently does nothing on the next call.
            data = {
                "contact": match,
                "contact_id": match["id"] if match else None,
            }
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    **data,
                },
            }

        if tool_name == "crm_contact_upsert":
            email = arguments.get("email", "")
            name = arguments.get("name", "")
            cid = f"rem_{abs(hash(email)) % 10000}"
            CRM_CONTACTS[cid] = {"id": cid, "email": email, "name": name}
            data = {"contact_id": cid, "status": "upserted"}
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    **data,
                },
            }

        if tool_name == "crm_note_create":
            # Accept the platform's canonical field names (contact_ref/body) as
            # primary, but keep the old names as a fallback so this still works
            # if you're calling it by hand for a quick test.
            contact_ref = arguments.get("contact_ref") or arguments.get("attendee_ref")
            note_content = arguments.get("body") or arguments.get("note", "")
            note_record = {
                "note_id": f"note_{len(CRM_NOTES) + 1}",
                "contact_ref": contact_ref,
                "body": note_content,
            }
            CRM_NOTES.append(note_record)
            data = {"note_id": note_record["note_id"], "status": "created"}
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    **data,
                },
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

            first_match = matches[0] if matches else KB_DOCS[1]

            # app/core/sagas.py:_kb_doc_get_args does documents[0]["doc_id"]
            # (a direct key access, not .get) whenever it chains kb.search ->
            # kb.document.get. Without "doc_id" on every returned document,
            # that chained call raises a KeyError. Add it alongside id/doc_ref.
            def with_doc_id(doc: dict[str, Any]) -> dict[str, Any]:
                return {**doc, "doc_id": doc["id"]}

            matches_out = [with_doc_id(d) for d in matches]

            payload = {
                "documents": matches_out,
                "doc_refs": [d["id"] for d in matches],
                "doc_ref": first_match["id"],
                "doc_id": first_match["id"],
                "answer": first_match["content"],
                "content": first_match["content"],
                "title": first_match["title"],
                "facts": {
                    "documents": matches_out,
                    "answer": first_match["content"],
                    "content": first_match["content"],
                    "title": first_match["title"],
                },
            }

            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    # 1. MCP Standard Compliant wrapper
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": False,
                    # 2. Raw fields for direct dictionary unpackers
                    **payload,
                },
            }

        if tool_name == "kb_document_get":
            target_ref = arguments.get("doc_ref") or arguments.get("id") or "doc_102"
            doc = next(
                (d for d in KB_DOCS if d.get("id") == target_ref or d.get("doc_ref") == target_ref),
                KB_DOCS[1],
            )
            payload = {
                "document": doc,
                "documents": [doc],
                "doc_ref": doc["id"],
                "doc_id": doc["id"],
                "title": doc["title"],
                "content": doc["content"],
                "answer": doc["content"],
                "facts": {
                    "documents": [doc],
                    "answer": doc["content"],
                    "content": doc["content"],
                },
            }
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": False,
                    **payload,
                },
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