"""Authorization monitoring for explicitly submitted Oura research packages."""

import secrets

from .service import ServiceError, call_service
from .storage import profile_job_lock
from .vault import read_secret, write_secret


def renew_authorization(source, config, *, required=False, credentials=None):
    """Send an access token only for a confirmed contribution or an existing monitor."""
    from .oura_api import _access

    connection = read_secret(source, "research")
    if not connection or connection.get("delete_pending") or not connection.get("secret"):
        if required:
            raise ServiceError("Complete research enrollment before sending Oura data.")
        return
    if not required and not connection.get("oura_monitoring"):
        return
    with profile_job_lock(source, "oura"):
        credentials = credentials or read_secret(source, "oura")
        if not credentials or credentials.get("reauth_required"):
            raise ServiceError("Reconnect Oura before contributing its research data.")
        _access(source, config, credentials, refresh=not credentials.get("authorization_proof"))
        # Keep the deletion receipt before sending a binding, including a lost response.
        with profile_job_lock(source, "credentials"):
            connection = read_secret(source, "research")
            if not connection or connection.get("delete_pending") or not connection.get("secret"):
                raise ServiceError("Research withdrawal is pending.")
            connection.setdefault("deletion_secret", secrets.token_urlsafe(32))
            connection["oura_monitoring"] = True
            write_secret(source, "research", connection)
        result = call_service(config, "/research/oura-authorization", {
            "access_token": credentials["access_token"],
            "authorization_proof": credentials.get("authorization_proof", ""),
            "receipt_secret": connection["deletion_secret"],
        }, token=connection["id"] + ":" + connection["secret"])
        if result.get("state") != "active":
            raise ServiceError("Oura research use is paused because access could not be verified.")


def withdraw_on_disconnect(source):
    """Withdraw the whole encrypted contribution; a package cannot be split remotely."""
    from .submissions import queue_deletion, retry_deletion

    connection = read_secret(source, "research")
    if not connection or not connection.get("oura_monitoring"):
        return ""
    queue_deletion(source)
    try:
        complete = retry_deletion(source)
    except ServiceError:
        return " Research deletion is queued; it will retry when online."
    return " Research contributions deleted." if complete else " Research-copy deletion is pending."
