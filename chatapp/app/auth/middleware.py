"""Authentication middleware for FastAPI.

This module provides middleware for protecting routes with Cognito authentication,
handling session validation, and automatic token refresh.
"""

import json
import logging
from typing import Callable, Optional
from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

from app.auth.cognito import (
    CognitoAuth,
    TokenExpiredError,
    TokenValidationError,
    UserInfo,
    get_user_groups,
    is_admin,
)
from app.config import get_config


# Session cookie name
SESSION_COOKIE_NAME = "chatapp_session"

# Routes that don't require authentication
PUBLIC_ROUTES = {
    "/",
    "/health",
    "/auth/login",
    "/auth/callback",
    "/docs",
    "/openapi.json",
    "/redoc",
    # Streaming diagnostic endpoint — emits 10 SSE chunks over 10s with no
    # auth required. Used to isolate where stream buffering happens between
    # origin → Lambda Web Adapter → CloudFront → browser.
    "/api/test-stream",
}

# Route prefixes that don't require authentication
PUBLIC_PREFIXES = [
    "/static/",
]

# Route prefixes that require admin group membership
ADMIN_PREFIXES = [
    "/admin",
]


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for handling authentication on protected routes.
    
    This middleware:
    - Checks for valid session cookie on protected routes
    - Validates JWT tokens from the session
    - Attempts token refresh if access token is expired
    - Redirects to login if no valid session or refresh fails
    
    Attributes:
        cognito: CognitoAuth instance for token operations
    """

    def __init__(self, app, cognito: Optional[CognitoAuth] = None):
        """Initialize AuthMiddleware.
        
        Args:
            app: FastAPI application
            cognito: Optional CognitoAuth instance (creates default if not provided)
        """
        super().__init__(app)
        self.cognito = cognito or CognitoAuth()

    def _is_api_route(self, path: str) -> bool:
        """Check if a route is an API route (expects JSON responses).
        
        Args:
            path: Request path
            
        Returns:
            True if route is an API route
        """
        return path.startswith("/api/")

    def _is_admin_route(self, path: str) -> bool:
        """Check if a route requires admin privileges.
        
        Args:
            path: Request path
            
        Returns:
            True if route requires admin group membership
        """
        for prefix in ADMIN_PREFIXES:
            if path.startswith(prefix):
                return True
        return False

    async def _apply_authorization(
        self,
        request: Request,
        user_info: UserInfo,
        session_data: dict,
        is_api: bool,
    ) -> Optional[Response]:
        """Fetch the user's groups, set ``request.state.is_admin``, and enforce
        admin-route authorization.

        This runs on both the normal (valid access token) path and the
        token-refresh path. Running it on both is what keeps admin status
        consistent: if it only ran on the normal path, any request served via
        an automatic token refresh (e.g. the first page load after an access
        token expires overnight) would leave ``is_admin`` unset and defaulting
        to ``False``, silently hiding admin-only UI such as the admin menu links.

        Args:
            request: Incoming request (mutated with ``state.is_admin``)
            user_info: Validated user info for the current token
            session_data: Session dict used as a fallback source for username
            is_api: Whether this is an API route (affects forbidden response)

        Returns:
            A Response if the request must be short-circuited (a non-admin
            hitting an admin route); otherwise ``None``.
        """
        user_groups: list = []
        username_for_groups = (
            user_info.username or user_info.email or session_data.get("username")
        )
        logger.debug(
            f"Group lookup: username={user_info.username}, email={user_info.email}, "
            f"session_username={session_data.get('username')}, using={username_for_groups}"
        )

        if username_for_groups:
            try:
                user_groups = await get_user_groups(username_for_groups)
                logger.debug(f"Fetched groups for {username_for_groups}: {user_groups}")
            except Exception as e:
                # Failed to fetch groups - log the error and default to no groups
                logger.error(
                    f"Failed to fetch groups for {username_for_groups}: {e}",
                    exc_info=True,
                )
                user_groups = []
        else:
            logger.warning(
                f"No username available for group lookup (user_id={user_info.user_id})"
            )

        # Set is_admin flag for all routes (used by templates)
        is_admin_user = is_admin(user_groups or [])
        request.state.is_admin = is_admin_user
        logger.info(
            f"Auth check: user={user_info.username or user_info.email}, "
            f"is_admin={is_admin_user}, groups={user_groups}, path={request.url.path}"
        )

        # Check admin authorization for admin routes
        if self._is_admin_route(request.url.path) and not is_admin_user:
            return self._handle_forbidden(request, is_api)

        return None

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Process request through authentication middleware.
        
        Args:
            request: Incoming request
            call_next: Next middleware/handler in chain
            
        Returns:
            Response from handler or redirect to login
        """
        # Check if route is public
        if self._is_public_route(request.url.path):
            return await call_next(request)

        # Check for dev mode - bypass auth entirely
        try:
            config = get_config()
            if config.dev_mode:
                # Create mock user info for dev mode
                request.state.user = UserInfo(
                    user_id=config.dev_user_id,
                    email="dev@localhost",
                    username="dev-user",
                )
                request.state.session = {
                    "access_token": "dev-token",
                    "id_token": "dev-id-token",
                    "refresh_token": "dev-refresh-token",
                }
                # In dev mode, grant admin access for testing
                request.state.is_admin = True
                logger.debug("Dev mode: granting admin access")
                return await call_next(request)
        except Exception:
            pass  # Config not loaded yet, continue with normal auth

        # Determine if this is an API route (needs JSON response vs HTML redirect)
        is_api = self._is_api_route(request.url.path)

        # Get session from cookie
        session_data = self._get_session_from_cookie(request)
        
        if not session_data:
            return self._handle_auth_failure(request, is_api, "no_session")

        # Validate access token
        try:
            user_info = self.cognito.validate_token(
                session_data.get("access_token", ""),
                id_token=session_data.get("id_token"),
            )
            # Attach user info to request state
            request.state.user = user_info
            request.state.session = session_data

            # Compute admin status + enforce admin-route authorization.
            # Shared with the token-refresh path so is_admin is always set.
            forbidden = await self._apply_authorization(
                request, user_info, session_data, is_api
            )
            if forbidden is not None:
                return forbidden

            return await call_next(request)
            
        except TokenExpiredError:
            # Try to refresh the token
            return await self._handle_token_refresh(
                request, call_next, session_data, is_api
            )
            
        except TokenValidationError:
            # Invalid token - return 401 for API routes, redirect for pages
            return self._handle_auth_failure(request, is_api, "invalid_session")

    def _is_public_route(self, path: str) -> bool:
        """Check if a route is public (doesn't require auth).
        
        Args:
            path: Request path
            
        Returns:
            True if route is public
        """
        if path in PUBLIC_ROUTES:
            return True
            
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True
                
        return False

    def _get_session_from_cookie(self, request: Request) -> Optional[dict]:
        """Extract session data from cookie.
        
        Args:
            request: Incoming request
            
        Returns:
            Session data dict or None if no valid session
        """
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        if not cookie_value:
            return None
            
        try:
            session_data = json.loads(cookie_value)
            
            # Refresh token is in a separate cookie - don't merge it into session_data
            # to keep the main cookie under 4KB limit
            refresh_cookie = request.cookies.get(f"{SESSION_COOKIE_NAME}_refresh")
            if refresh_cookie:
                # Store refresh token separately in request state for token refresh
                request.state.refresh_token = refresh_cookie
            
            return session_data
        except (json.JSONDecodeError, ValueError):
            return None

    def _handle_auth_failure(
        self, request: Request, is_api: bool, error: str
    ) -> Response:
        """Handle authentication failure appropriately based on route type.
        
        Args:
            request: Original request
            is_api: Whether this is an API route
            error: Error code/message
            
        Returns:
            JSON 401 response for API routes, redirect for page routes
        """
        if is_api:
            # Return JSON 401 for API routes so frontend can handle gracefully
            from fastapi.responses import JSONResponse
            response = JSONResponse(
                status_code=401,
                content={
                    "detail": "Session expired",
                    "error": error,
                    "redirect": "/auth/login",
                }
            )
            response.delete_cookie(SESSION_COOKIE_NAME)
            return response
        else:
            # Redirect to login for page routes
            return self._redirect_to_login(request, error)

    def _handle_forbidden(
        self, request: Request, is_api: bool
    ) -> Response:
        """Handle authorization failure (user lacks required permissions).
        
        Args:
            request: Original request
            is_api: Whether this is an API route
            
        Returns:
            JSON 403 response for API routes, redirect to chat for page routes
        """
        from fastapi.responses import JSONResponse
        
        if is_api:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Access denied",
                    "error": "admin_required",
                    "message": "You must be an administrator to access this resource.",
                }
            )
        else:
            # Redirect non-admin users to home page
            return RedirectResponse(url="/home", status_code=302)

    def _redirect_to_login(
        self, request: Request, error: Optional[str] = None
    ) -> RedirectResponse:
        """Create redirect response to login page.
        
        Args:
            request: Original request
            error: Optional error message to include
            
        Returns:
            RedirectResponse to login
        """
        login_url = "/auth/login"
        if error:
            login_url = f"{login_url}"
            
        response = RedirectResponse(url=login_url, status_code=302)
        # Clear any existing session cookie
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response

    async def _handle_token_refresh(
        self,
        request: Request,
        call_next: Callable,
        session_data: dict,
        is_api: bool = False,
    ) -> Response:
        """Attempt to refresh expired access token.
        
        Args:
            request: Original request
            call_next: Next handler in chain
            session_data: Current session data
            is_api: Whether this is an API route
            
        Returns:
            Response from handler with updated session or redirect to login
        """
        # Get refresh token from request state (stored separately from session_data)
        refresh_token = getattr(request.state, "refresh_token", None)
        username = session_data.get("username")
        if not refresh_token or not username:
            return self._handle_auth_failure(request, is_api, "session_expired")

        try:
            # Refresh the tokens
            token_response = await self.cognito.refresh_tokens(refresh_token, username)
            
            # Update session data (without refresh token - keep it separate)
            new_session = {
                "access_token": token_response.access_token,
                "id_token": token_response.id_token,
                "username": username,
            }
            
            # Validate the new token
            user_info = self.cognito.validate_token(
                token_response.access_token,
                id_token=token_response.id_token,
            )
            request.state.user = user_info
            request.state.session = new_session
            
            # Store new refresh token in request state
            request.state.refresh_token = token_response.refresh_token or refresh_token

            # Compute admin status + enforce admin-route authorization on the
            # refreshed session, exactly as the normal path does. Without this,
            # is_admin would default to False on the first load after the
            # access token expires, dropping the admin menu items.
            forbidden = await self._apply_authorization(
                request, user_info, new_session, is_api
            )
            if forbidden is not None:
                return forbidden

            # Continue with the request
            response = await call_next(request)
            
            # Update the session cookies (main + refresh token separate)
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=json.dumps(new_session),
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=86400 * 30,
            )
            if token_response.refresh_token:
                response.set_cookie(
                    key=f"{SESSION_COOKIE_NAME}_refresh",
                    value=token_response.refresh_token,
                    httponly=True,
                    secure=True,
                    samesite="lax",
                    max_age=86400 * 30,
                )
            
            return response
            
        except Exception:
            # Refresh failed - return appropriate response based on route type
            return self._handle_auth_failure(request, is_api, "session_expired")

def get_current_user(request: Request) -> UserInfo:
    """Get current authenticated user from request.
    
    Args:
        request: Request with user attached by middleware
        
    Returns:
        UserInfo for authenticated user
        
    Raises:
        ValueError: If no user is attached to request
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise ValueError("No authenticated user found in request")
    return user


def get_session_data(request: Request) -> dict:
    """Get session data from request.
    
    Args:
        request: Request with session attached by middleware
        
    Returns:
        Session data dict
        
    Raises:
        ValueError: If no session is attached to request
    """
    session = getattr(request.state, "session", None)
    if not session:
        raise ValueError("No session found in request")
    return session
