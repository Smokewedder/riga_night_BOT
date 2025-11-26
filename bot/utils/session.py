import asyncio
from bot.config import ADMIN_IDS

user_sessions = {}
session_locks = {}  # Add locks for each user session

def get_user_lock(user_id: int):
    """Get or create a lock for a specific user session."""
    if user_id not in session_locks:
        session_locks[user_id] = asyncio.Lock()
    return session_locks[user_id]

async def safe_session_operation(user_id: int, operation):
    """Safely perform operations on user sessions with locking."""
    lock = get_user_lock(user_id)
    async with lock:
        return await operation()

async def delete_client_messages(user_id: int, context):
    """Deletes all tracked messages for a given client and clears their session."""
    try:
        session_data = user_sessions.get(user_id, None)
        if session_data:
            all_messages_to_delete = []
            all_messages_to_delete.extend(session_data.get("client_messages", []))
            all_messages_to_delete.extend(session_data.get("user_messages", []))

            # Delete messages with better error handling
            for msg_id in all_messages_to_delete:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception as e:
                    # Do not log user or message IDs to avoid leaking PII
                    # Just silently continue if message doesn't exist or can't be deleted
                    pass
            
            # Clear the message lists but keep the session
            session_data["client_messages"] = []
            session_data["user_messages"] = []
    except Exception as e:
        print(f"[ERROR] Error in delete_client_messages for user {user_id}: {e}")

async def cleanup_user_session(user_id: int, context):
    """Safely cleanup a user session with proper locking."""
    async def _cleanup():
        try:
            if user_id in user_sessions:
                await delete_client_messages(user_id, context)
                user_sessions.pop(user_id, None)
            if user_id in session_locks:
                session_locks.pop(user_id, None)
        except Exception as e:
            print(f"[ERROR] Error cleaning up session for user {user_id}: {e}")
    
    await safe_session_operation(user_id, _cleanup)

def is_session_active(user_id: int) -> bool:
    """Check if a user has an active session."""
    return user_id in user_sessions and user_sessions[user_id].get("step") is not None

def get_session_flow_type(user_id: int) -> str:
    """Get the flow type of a user's session."""
    if user_id in user_sessions:
        return user_sessions[user_id].get("flow_type", "unknown")
    return "none"
