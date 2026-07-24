"""Database tools — owned by the User Management Agent.

Every tool returns a JSON-serialisable dict with a "status" key so the model can
recover from failures instead of hallucinating success. Docstrings and type
hints are the tool contract: the Gen AI SDK derives the function declaration
from them, so they are written for the model to read.
"""

import re
from typing import Optional

from ..db.database import (
    EDITABLE_FIELDS,
    find_user,
    init_db,
    insert_user,
    select_users,
    update_user_fields,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def create_user(
    name: str,
    email: str,
    phone: Optional[str] = None,
    department: Optional[str] = None,
    skills: Optional[str] = None,
) -> dict:
    """Create a new user record in the database.

    Args:
        name: Full name of the user, e.g. "Ada Lovelace".
        email: Unique email address. Must be a valid address.
        phone: Optional contact phone number.
        department: Optional department or niche, e.g. "Data Science".
        skills: Optional comma-separated list of skills, e.g. "Python, SQL".

    Returns:
        A dict with status "success" and the created user, or status "error"
        with a message explaining why the user could not be created.
    """
    init_db()
    if not name or not name.strip():
        return {"status": "error", "message": "name is required and cannot be empty."}
    if not EMAIL_RE.match(email or ""):
        return {"status": "error", "message": f"'{email}' is not a valid email address."}
    if find_user(email=email):
        return {
            "status": "error",
            "message": f"A user with email '{email}' already exists. Use edit_user to update them.",
        }
    user = insert_user(
        name=name.strip(),
        email=email.strip(),
        phone=phone,
        department=department,
        skills=skills,
    )
    return {"status": "success", "message": f"User '{name}' created.", "user": user}


def get_all_users(limit: Optional[int] = None) -> dict:
    """Retrieve the list of all users currently stored in the database.

    Args:
        limit: Optional maximum number of users to return. Omit to return all.

    Returns:
        A dict with status "success", the number of users found, and the list
        of user records.
    """
    init_db()
    users = select_users(limit=limit)
    return {"status": "success", "count": len(users), "users": users}


def edit_user(
    user_id: Optional[int] = None,
    email: Optional[str] = None,
    new_name: Optional[str] = None,
    new_email: Optional[str] = None,
    new_phone: Optional[str] = None,
    new_department: Optional[str] = None,
    new_skills: Optional[str] = None,
) -> dict:
    """Update the profile fields of an existing user.

    Identify the user by either user_id or their current email address. Provide
    only the fields that should change; omitted fields are left untouched.

    Args:
        user_id: ID of the user to update. Preferred identifier.
        email: Current email of the user, used when user_id is unknown.
        new_name: New full name.
        new_email: New email address.
        new_phone: New phone number.
        new_department: New department or niche.
        new_skills: New comma-separated skills list.

    Returns:
        A dict with status "success" and the updated user record, or status
        "error" with a message.
    """
    init_db()
    if user_id is None and not email:
        return {
            "status": "error",
            "message": "Provide either user_id or email to identify the user to edit.",
        }
    user = find_user(user_id=user_id, email=email)
    if not user:
        target = f"id={user_id}" if user_id is not None else f"email={email}"
        return {"status": "error", "message": f"No user found for {target}."}

    updates = {
        "name": new_name,
        "email": new_email,
        "phone": new_phone,
        "department": new_department,
        "skills": new_skills,
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return {
            "status": "error",
            "message": "No new values supplied. Provide at least one field to change: "
            + ", ".join(EDITABLE_FIELDS),
        }
    if "email" in updates:
        if not EMAIL_RE.match(updates["email"]):
            return {"status": "error", "message": f"'{updates['email']}' is not a valid email address."}
        clash = find_user(email=updates["email"])
        if clash and clash["id"] != user["id"]:
            return {
                "status": "error",
                "message": f"Email '{updates['email']}' is already used by user id {clash['id']}.",
            }

    updated = update_user_fields(user["id"], updates)
    return {
        "status": "success",
        "message": f"Updated {', '.join(updates)} for user id {user['id']}.",
        "user": updated,
    }
