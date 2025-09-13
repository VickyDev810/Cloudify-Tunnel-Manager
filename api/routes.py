from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Callable
from core.login import run_command_live, get_current_status
from config.manager_frontend import UniversalTunnelManager
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from pathlib import Path
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
import secrets
from datetime import datetime
from fastapi.staticfiles import StaticFiles




# --- Config ---
USER_DB_FILE = Path("users.json")

# --- Password hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Token store (in-memory) ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
active_tokens = {}

# --- Helpers for user storage ---
def load_users():
    if USER_DB_FILE.exists():
        with open(USER_DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USER_DB_FILE, "w") as f:
        json.dump(users, f, indent=4)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# --- Authentication dependency ---
async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Verify token and return current user"""
    username = active_tokens.get(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    users = load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

# --- Optional: Admin-only dependency ---
async def get_admin_user(current_user: dict = Depends(get_current_user)):
    """Check if user is admin (optional - customize as needed)"""
    # You can add an 'is_admin' field to users if needed
    # if not current_user.get("is_admin", False):
    #     raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


app = FastAPI()




origins = [
    "*",  # You can replace "*" with a list of specific domains like ["http://localhost:3000"]
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # origins that are allowed
    allow_credentials=True,
    allow_methods=["*"],    # allow all HTTP methods
    allow_headers=["*"],    # allow all headers
)

server = None
shutdown_callback: Optional[Callable] = None



def set_server(server_instance):
    global server
    server = server_instance

def set_shutdown_callback(callback: Callable):
    """Set a callback function to be called when shutdown is requested"""
    global shutdown_callback
    shutdown_callback = callback

# --- Request Models ---
class TunnelCreateRequest(BaseModel):
    tunnel_name: str
    auto_start_on_create: Optional[bool] = True
    confirm_autostart: Optional[bool] = None

class TunnelDeleteRequest(BaseModel):
    tunnel_name: str
    force: Optional[bool] = False

class RouteAddRequest(BaseModel):
    tunnel_name: str
    domain: str
    end_user_url: str
    localhost_port: int

class RouteRemoveRequest(BaseModel):
    tunnel_name: str
    domain: str

class TunnelNameRequest(BaseModel):
    tunnel_name: str

class AdoptTunnelRequest(BaseModel):
    tunnel_name: str

class RegisterRequest(BaseModel):
    username: str
    password: str

# --- PUBLIC ENDPOINTS (No auth required) ---




@app.get("/setup/check")
def check_setup_status():
    """Check if initial setup is needed - PUBLIC endpoint"""
    users = load_users()
    has_users = len(users) > 0
    
    # Check if cloudflare is authenticated (optional)
    try:
        # Check if tunnel exists
        response = os.popen("cloudflared tunnel list").read()
        has_tunnels = "No tunnels" not in response and response.strip() != ""
    except:
        has_tunnels = False
    
    return {
        "needs_setup": not has_users,
        "has_users": has_users,
        "has_tunnels": has_tunnels,
        "user_count": len(users)
    }

@app.post("/auth/register")
def register_user(req: RegisterRequest):
    """Register a new user - PUBLIC endpoint"""
    users = load_users()
    
    # If no users exist, first user becomes admin
    is_first_user = len(users) == 0
    
    if req.username in users:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    users[req.username] = {
        "username": req.username,
        "hashed_password": get_password_hash(req.password),
        "is_admin": is_first_user,  # First user is admin
        "created_at": datetime.now().isoformat()
    }
    save_users(users)
    
    return {
        "message": "User registered successfully",
        "is_admin": is_first_user
    }

@app.post("/auth/login")
def login_user(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token - PUBLIC endpoint"""
    users = load_users()
    user = users.get(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate token
    token = secrets.token_hex(32)  # Made longer for better security
    active_tokens[token] = user["username"]
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/logout")
def logout_user(token: str = Depends(oauth2_scheme)):
    """Logout and invalidate token"""
    if token in active_tokens:
        del active_tokens[token]
    return {"message": "Logged out successfully"}

# --- PROTECTED ENDPOINTS (Auth required) ---

@app.get("/auth/me")
def read_users_me(current_user: dict = Depends(get_current_user)):
    """Get current user info - PROTECTED endpoint"""
    return {
        "username": current_user["username"],
        # Don't return the hashed password
    }

@app.post("/initial_setup")
async def run_command(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Initial setup - PROTECTED endpoint"""
    background_tasks.add_task(run_login_then_create_tunnel)
    return {"message": "Command started", "user": current_user["username"]}

def run_login_then_create_tunnel():
    cmd = ["cloudflared", "tunnel", "login"]
    run_command_live(cmd)  # blocks until complete
    
@app.get("/status")
async def get_status(current_user: dict = Depends(get_current_user)):
    """Get status - PROTECTED endpoint"""
    status_data = get_current_status()
    if status_data is None:
        return JSONResponse(status_code=404, content={"error": "Status not found"})
    return status_data

# --- UniversalTunnelManager API - ALL PROTECTED ---

@app.get("/tunnels")
def list_tunnels(current_user: dict = Depends(get_current_user)):
    """List all tunnels - PROTECTED endpoint"""
    manager = UniversalTunnelManager()
    tunnels = manager.list_all_tunnels()
    return {"tunnels": tunnels, "user": current_user["username"]}

@app.post("/tunnel/create")
def create_tunnel(
    req: TunnelCreateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create tunnel - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name, auto_start_on_create=req.auto_start_on_create)
    success = manager.create_tunnel(
        tunnel_name=req.tunnel_name,
        setup_auto_start=req.confirm_autostart,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to create tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' created", "user": current_user["username"]}

@app.post("/tunnel/delete")
def delete_tunnel(
    req: TunnelDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    """Delete tunnel - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    success = manager.delete_tunnel(req.tunnel_name, force=req.force)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' deleted", "user": current_user["username"]}

@app.post("/tunnel/adopt")
def adopt_tunnel(
    req: AdoptTunnelRequest,
    current_user: dict = Depends(get_current_user)
):
    """Adopt tunnel - PROTECTED endpoint"""
    manager = UniversalTunnelManager()
    success = manager.adopt_tunnel(req.tunnel_name)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to adopt tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' adopted", "user": current_user["username"]}

@app.get("/tunnel/routes")
def list_routes(
    tunnel_name: str = Query(..., description="Tunnel name"),
    current_user: dict = Depends(get_current_user)
):
    """List routes - PROTECTED endpoint"""
    manager = UniversalTunnelManager(tunnel_name)
    routes = manager.list_routes(tunnel_name)
    return {"routes": routes, "user": current_user["username"]}

@app.post("/tunnel/route/add")
def add_route(
    req: RouteAddRequest,
    current_user: dict = Depends(get_current_user)
):
    """Add route - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    success = manager.add_route(req.domain, req.localhost_port, req.end_user_url, req.tunnel_name)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to add route")
    return {
        "message": f"Route added: {req.domain} -> localhost:{req.localhost_port}",
        "user": current_user["username"]
    }

@app.post("/tunnel/route/remove")
def remove_route(
    req: RouteRemoveRequest,
    current_user: dict = Depends(get_current_user)
):
    """Remove route - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    success = manager.remove_route(req.domain, req.tunnel_name)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to remove route")
    return {"message": f"Route removed: {req.domain}", "user": current_user["username"]}

@app.get("/tunnel/status")
def tunnel_status(
    tunnel_name: str = Query(..., description="Tunnel name"),
    current_user: dict = Depends(get_current_user)
):
    """Get tunnel status - PROTECTED endpoint"""
    manager = UniversalTunnelManager(tunnel_name)
    routes = manager.list_routes(tunnel_name)
    status = manager._check_tunnel_status(tunnel_name)
    return {
        "tunnel": tunnel_name,
        "status": status,
        "routes": routes,
        "user": current_user["username"]
    }

@app.post("/tunnel/start")
def start_tunnel(
    req: TunnelNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """Start tunnel - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    manager.start_tunnel(req.tunnel_name)
    return {"message": f"Tunnel '{req.tunnel_name}' started", "user": current_user["username"]}

@app.post("/tunnel/stop")
def stop_tunnel(
    req: TunnelNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """Stop tunnel - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    manager.stop_tunnel(req.tunnel_name)
    return {"message": f"Tunnel '{req.tunnel_name}' stopped", "user": current_user["username"]}

@app.post("/tunnel/autostart/setup")
def setup_autostart(
    req: TunnelNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """Setup autostart - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    success = manager.setup_autostart()
    if not success:
        raise HTTPException(status_code=400, detail="Failed to set up autostart")
    return {
        "message": f"Autostart enabled for tunnel '{req.tunnel_name}'",
        "user": current_user["username"]
    }

@app.post("/tunnel/autostart/remove")
def remove_autostart(
    req: TunnelNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """Remove autostart - PROTECTED endpoint"""
    manager = UniversalTunnelManager(req.tunnel_name)
    success = manager.remove_autostart()
    if not success:
        raise HTTPException(status_code=400, detail="Failed to remove autostart")
    return {
        "message": f"Autostart removed for tunnel '{req.tunnel_name}'",
        "user": current_user["username"]
    }

@app.post("/shutdown")
async def shutdown(current_user: dict = Depends(get_current_user)):
    """Shutdown server - PROTECTED endpoint (consider making admin-only)"""
    # You might want to use get_admin_user here instead
    if server:
        server.should_exit = True
    return {"message": "Server shutting down...", "user": current_user["username"]}

# --- Optional: Token cleanup endpoint ---
@app.post("/auth/cleanup-tokens")
async def cleanup_tokens(current_user: dict = Depends(get_current_user)):
    """Clean up expired tokens - PROTECTED endpoint"""
    # In a production system, you'd want to track token creation time
    # and remove expired ones. For now, this is a placeholder.
    return {
        "message": "Token cleanup not implemented yet",
        "active_tokens": len(active_tokens)
    }

static_dir = Path(__file__).parent / 'static'
print(static_dir)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

