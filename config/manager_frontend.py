#!/usr/bin/env python3
"""
Universal Cloudflare Tunnel Manager with State Tracking
Works across different files and sessions with persistent state
No sudo required - uses user services and process management
"""

import subprocess
import os
import sys
import yaml
import time
import json
import signal
import threading
from typing import Dict, List, Optional, Union
from datetime import datetime
import atexit

class UniversalTunnelManager:
    """
    Universal Cloudflare Tunnel Manager with persistent state tracking
    Can be used from any Python file and maintains state across sessions
    """
    
    # Class variable for state file location
    STATE_FILE = os.path.expanduser("~/.cloudflared/tunnel_manager_state.json")
    LOCK_FILE = os.path.expanduser("~/.cloudflared/tunnel_manager.lock")
    
    def __init__(self, tunnel_name: str = None, auto_start_on_create: bool = True):
        """
        Initialize the Universal Tunnel Manager
        
        Args:
            tunnel_name: Name of the tunnel to manage (None = show all tunnels)
            auto_start_on_create: Whether to set up auto-start when creating tunnel
        """
        self.config_dir = os.path.expanduser("~/.cloudflared")
        os.makedirs(self.config_dir, exist_ok=True)
        
        self.tunnel_name = tunnel_name
        self.auto_start_on_create = auto_start_on_create
        
        # Initialize state
        self._init_state()
        
        # If tunnel name provided, set it as current
        if tunnel_name:
            self.config_file = os.path.join(self.config_dir, f"config-{tunnel_name}.yml")
            self._register_tunnel(tunnel_name)
        else:
            self.config_file = None
        
        # Register cleanup on exit
        atexit.register(self._cleanup_on_exit)
    
    def _init_state(self):
        """Initialize or load the state file"""
        if not os.path.exists(self.STATE_FILE):
            initial_state = {
                "tunnels": {},
                "current_tunnel": None,
                "last_updated": datetime.now().isoformat()
            }
            self._save_state(initial_state)
    
    def _load_state(self) -> Dict:
        """Load state from JSON file"""
        try:
            with open(self.STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"tunnels": {}, "current_tunnel": None}
    
    def _save_state(self, state: Dict):
        """Save state to JSON file"""
        state["last_updated"] = datetime.now().isoformat()
        with open(self.STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    
    def _register_tunnel(self, tunnel_name: str):
        """Register a tunnel in the state"""
        state = self._load_state()
        
        if tunnel_name not in state["tunnels"]:
            state["tunnels"][tunnel_name] = {
                "created_at": datetime.now().isoformat(),
                "config_file": os.path.join(self.config_dir, f"config-{tunnel_name}.yml"),
                "routes": [],
                "auto_start": False,
                "status": "unknown"
            }
        
        state["current_tunnel"] = tunnel_name
        self._save_state(state)
    
    def _update_tunnel_info(self, tunnel_name: str, info: Dict):
        """Update tunnel information in state"""
        state = self._load_state()
        if tunnel_name in state["tunnels"]:
            state["tunnels"][tunnel_name].update(info)
            self._save_state(state)
    
    def _unregister_tunnel(self, tunnel_name: str):
        """Remove a tunnel from state"""
        state = self._load_state()
        if tunnel_name in state["tunnels"]:
            del state["tunnels"][tunnel_name]
            if state["current_tunnel"] == tunnel_name:
                state["current_tunnel"] = None
            self._save_state(state)
    
    def _cleanup_on_exit(self):
        """Cleanup on script exit"""
        if self.tunnel_name:
            self._update_tunnel_info(self.tunnel_name, {"last_accessed": datetime.now().isoformat()})
    
    def _detect_environment(self) -> str:
        """Detect if running in Docker, EC2, or regular system"""
        # Check for Docker
        if os.path.exists('/.dockerenv') or os.path.exists('/proc/self/cgroup'):
            try:
                with open('/proc/self/cgroup', 'r') as f:
                    if 'docker' in f.read():
                        return "docker"
            except:
                pass
        
        # Check for EC2
        try:
            result = subprocess.run(['curl', '-s', '--connect-timeout', '2', 
                                   'http://169.254.169.254/latest/meta-data/instance-id'], 
                                  capture_output=True, text=True)
            if result.returncode == 0 and result.stdout:
                return "ec2"
        except:
            pass
        
        # Check for common VPS providers
        try:
            with open('/proc/version', 'r') as f:
                version = f.read().lower()
                if 'microsoft' in version:
                    return "wsl"
                elif 'aws' in version:
                    return "ec2"
        except:
            pass
        
        return "local"
    
    def _has_systemd_user_support(self) -> bool:
        """Check if systemd user services are supported"""
        try:
            # Check if systemd is available
            subprocess.run(["systemctl", "--version"], capture_output=True, check=True)
            
            # Check if user services are enabled
            result = subprocess.run(["systemctl", "--user", "status"], capture_output=True)
            return result.returncode == 0
        except:
            return False
    
    def list_all_tunnels(self) -> List[Dict]:
        """List all tunnels managed by this system"""
        print("\n=== All Managed Tunnels ===")
        
        state = self._load_state()
        all_tunnels = []
        
        # Get actual tunnels from cloudflared
        result = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True
        )
        
        try:
            actual_tunnels = json.loads(result.stdout)
            if actual_tunnels is None:
                actual_tunnels = []
        except:
            actual_tunnels = []
        
        # Match with our managed tunnels
        for tunnel_name, info in state["tunnels"].items():
            tunnel_data = {
                "name": tunnel_name,
                "managed": True,
                "config_file": info["config_file"],
                "routes": info.get("routes", []),
                "auto_start": info.get("auto_start", False),
                "created_at": info.get("created_at", "unknown"),
                "exists": False,
                "id": None
            }
            
            # Check if tunnel actually exists
            for actual in actual_tunnels:
                if actual.get("name") == tunnel_name:
                    tunnel_data["exists"] = True
                    tunnel_data["id"] = actual.get("id", "unknown")
                    tunnel_data["status"] = self._check_tunnel_status(tunnel_name)
                    break
            
            all_tunnels.append(tunnel_data)
        
        # Display results
        if all_tunnels:
            print(f"\nFound {len(all_tunnels)} managed tunnel(s):\n")
            print(f"{'Name':<20} {'Status':<15} {'Routes':<8} {'Auto-start':<12} {'Exists'}")
            print("-" * 75)
            
            for tunnel in all_tunnels:
                status = tunnel.get("status", "stopped")
                routes = len(tunnel["routes"])
                auto = "Yes" if tunnel["auto_start"] else "No"
                exists = "Yes" if tunnel["exists"] else "No (deleted)"
                
                print(f"{tunnel['name']:<20} {status:<15} {routes:<8} {auto:<12} {exists}")
        else:
            print("No managed tunnels found.")
        
        # Show unmanaged tunnels
        print("\n=== Other Cloudflare Tunnels ===")
        unmanaged = []
        for actual in actual_tunnels:
            if actual.get("name") not in state["tunnels"]:
                unmanaged.append(actual)
        
        if unmanaged:
            print(f"\nFound {len(unmanaged)} unmanaged tunnel(s):")
            for tunnel in unmanaged:
                print(f"  • {tunnel.get('name')} (ID: {tunnel.get('id')})")
            print("\nTo manage these tunnels, use: manager.adopt_tunnel('tunnel-name')")
        else:
            print("No unmanaged tunnels found.")
        
        return all_tunnels
    
    def _check_tunnel_status(self, tunnel_name: str) -> str:
        """Check if a tunnel is running"""
        state = self._load_state()
        tunnel_info = state["tunnels"].get(tunnel_name, {})
        
        # Check user systemd service first
        if tunnel_info.get("auto_start") and self._has_systemd_user_support():
            result = subprocess.run(
                ["systemctl", "--user", "is-active", f"cloudflared-tunnel-{tunnel_name}"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return "running (service)"
        
        # Check for manual processes
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"cloudflared.*tunnel.*{tunnel_name}"],
                capture_output=True
            )
            if result.returncode == 0:
                return "running (manual)"
        except:
            pass
        
        # Check for temporary tunnels in state
        if tunnel_info.get("temp_process_id"):
            try:
                os.kill(tunnel_info["temp_process_id"], 0)
                return "running (temp)"
            except:
                # Process doesn't exist, clean up state
                self._update_tunnel_info(tunnel_name, {"temp_process_id": None})
        
        return "stopped"
    
    def use_tunnel(self, tunnel_name: str):
        """Switch to managing a different tunnel"""
        self.tunnel_name = tunnel_name
        self.config_file = os.path.join(self.config_dir, f"config-{tunnel_name}.yml")
        self._register_tunnel(tunnel_name)
        
        state = self._load_state()
        state["current_tunnel"] = tunnel_name
        self._save_state(state)
        
        print(f"Now managing tunnel: {tunnel_name}")
    
    def adopt_tunnel(self, tunnel_name: str) -> bool:
        """Adopt an existing unmanaged tunnel"""
        result = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True
        )
        
        try:
            tunnels = json.loads(result.stdout)
            tunnel_exists = any(t.get("name") == tunnel_name for t in tunnels)
            
            if not tunnel_exists:
                print(f"Tunnel '{tunnel_name}' not found")
                return False
            
            self._register_tunnel(tunnel_name)
            self.use_tunnel(tunnel_name)
            
            # Try to find existing config
            possible_configs = [
                os.path.join(self.config_dir, "config.yml"),
                os.path.join(self.config_dir, f"config-{tunnel_name}.yml"),
                os.path.join(self.config_dir, f"{tunnel_name}.yml")
            ]
            
            config_found = False
            for config_path in possible_configs:
                if os.path.exists(config_path):
                    if config_path != self.config_file:
                        import shutil
                        shutil.copy2(config_path, self.config_file)
                    config_found = True
                    break
            
            if config_found:
                config = self._load_config()
                routes = []
                if "ingress" in config:
                    for rule in config["ingress"]:
                        if isinstance(rule, dict) and "hostname" in rule:
                            routes.append({
                                "domain": rule["hostname"],
                                "service": rule["service"]
                            })
                
                self._update_tunnel_info(tunnel_name, {"routes": routes})
                print(f"Adopted tunnel '{tunnel_name}' with {len(routes)} routes")
            else:
                print(f"Adopted tunnel '{tunnel_name}' (no config found)")
            
            return True
            
        except:
            print(f"Failed to check tunnel existence")
            return False
    
    def get_current_tunnel(self) -> Optional[str]:
        """Get the currently selected tunnel"""
        if self.tunnel_name:
            return self.tunnel_name
        
        state = self._load_state()
        return state.get("current_tunnel")
    
    def login(self):
        """Login to Cloudflare using browser authentication"""
        print("Logging in to Cloudflare...")
        print("A browser window will open for authentication.")
        try:
            subprocess.run(["cloudflared", "tunnel", "login"], check=True)
            print("Login successful!")
            return True
        except subprocess.CalledProcessError:
            print("Login failed")
            return False
    
    def create_tunnel(self, tunnel_name: str = None, setup_auto_start: bool = None) -> bool:
        """Create a new tunnel"""
        if not tunnel_name:
            if self.tunnel_name:
                tunnel_name = self.tunnel_name
                if not tunnel_name:
                    print("Tunnel name required")
                    return False
        
        self.use_tunnel(tunnel_name)
        
        # Check if tunnel exists
        result = subprocess.run(
            ["cloudflared", "tunnel", "list"],
            capture_output=True,
            text=True
        )
        
        tunnel_exists = tunnel_name in result.stdout
        
        if tunnel_exists:
            print(f"Tunnel '{tunnel_name}' already exists")
            
            if setup_auto_start is None:
                setup_auto_start = self.auto_start_on_create
            
            
            
            return True
        
        print(f"Creating tunnel '{tunnel_name}'...")
        try:
            subprocess.run(
                ["cloudflared", "tunnel", "create", tunnel_name],
                check=True
            )
            print("Tunnel created successfully!")
            
            self._register_tunnel(tunnel_name)
            
            if setup_auto_start is None:
                setup_auto_start = self.auto_start_on_create
                
            if setup_auto_start:
                print("\nSetting up auto-start...")              
                self.setup_autostart()
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to create tunnel: {e}")
            return False
    
    def create_temp_tunnel(self, port: int, subdomain: str = None) -> Optional[str]:
        """
        Create a temporary tunnel using cloudflared tunnel --url
        Returns the generated URL
        """
        print(f"Creating temporary tunnel for localhost:{port}...")
        
        try:
            # Create temp tunnel name for tracking
            temp_name = f"temp-{int(time.time())}"
            
            # Start tunnel process
            cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
            if subdomain:
                cmd.extend(["--name", subdomain])
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for URL to be generated
            url = None
            timeout = 30
            start_time = time.time()
            
            print("Waiting for tunnel URL...")
            
            while time.time() - start_time < timeout:
                if process.poll() is not None:
                    # Process died
                    stderr = process.stderr.read()
                    print(f"Tunnel process failed: {stderr}")
                    return None
                
                # Check for URL in stderr (cloudflared outputs URL to stderr)
                try:
                    process.stderr.flush()
                    line = process.stderr.readline()
                    if line and ("https://" in line or "trycloudflare.com" in line):
                        # Extract URL from line
                        import re
                        url_match = re.search(r'https://[^\s]+', line)
                        if url_match:
                            url = url_match.group(0)
                            break
                except:
                    pass
                
                time.sleep(0.5)
            
            if not url:
                print("Failed to get tunnel URL within timeout")
                process.terminate()
                return None
            
            # Register temporary tunnel
            self._register_tunnel(temp_name)
            self._update_tunnel_info(temp_name, {
                "temp_tunnel": True,
                "temp_url": url,
                "temp_port": port,
                "temp_process_id": process.pid,
                "auto_start": False
            })
            
            print(f"Temporary tunnel created!")
            print(f"URL: {url}")
            print(f"Local: localhost:{port}")
            print(f"Process ID: {process.pid}")
            print("\nTo stop: manager.stop_temp_tunnel() or Ctrl+C")
            
            # Keep process reference
            setattr(self, f"_temp_process_{process.pid}", process)
            
            return url
            
        except Exception as e:
            print(f"Failed to create temporary tunnel: {e}")
            return None
    
    def stop_temp_tunnel(self, url: str = None):
        """Stop a temporary tunnel by URL or stop all temp tunnels"""
        state = self._load_state()
        
        stopped_any = False
        for tunnel_name, info in state["tunnels"].items():
            if info.get("temp_tunnel"):
                if url and info.get("temp_url") != url:
                    continue
                
                pid = info.get("temp_process_id")
                if pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        print(f"Stopped temporary tunnel: {info.get('temp_url', tunnel_name)}")
                        stopped_any = True
                    except:
                        print(f"Process {pid} not found (may have already stopped)")
                
                # Clean up from state
                self._unregister_tunnel(tunnel_name)
        
        if not stopped_any:
            if url:
                print(f"Temporary tunnel with URL {url} not found")
            else:
                print("No temporary tunnels running")
    
    def list_temp_tunnels(self):
        """List all running temporary tunnels"""
        state = self._load_state()
        
        temp_tunnels = []
        for tunnel_name, info in state["tunnels"].items():
            if info.get("temp_tunnel"):
                pid = info.get("temp_process_id")
                # Check if process is still running
                if pid:
                    try:
                        os.kill(pid, 0)  # Check if process exists
                        temp_tunnels.append(info)
                    except:
                        # Process dead, clean up
                        self._unregister_tunnel(tunnel_name)
        
        if temp_tunnels:
            print("\n=== Temporary Tunnels ===")
            print(f"{'URL':<50} {'Port':<8} {'PID'}")
            print("-" * 70)
            
            for tunnel in temp_tunnels:
                url = tunnel.get("temp_url", "unknown")
                port = tunnel.get("temp_port", "unknown")
                pid = tunnel.get("temp_process_id", "unknown")
                print(f"{url:<50} {port:<8} {pid}")
        else:
            print("No temporary tunnels running")
    
    def _load_config(self) -> Dict:
        """Load existing config or create new one"""
        if not self.config_file:
            return {}
            
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return yaml.safe_load(f) or {}
        return {}
    
    def _save_config(self, config: Dict):
        """Save config to file"""
        if not self.config_file:
            return
            
        with open(self.config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
    
    def _get_tunnel_id(self, tunnel_name: str = None) -> Optional[str]:
        """Get tunnel ID from cloudflared list"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            return None
            
        result = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True
        )
        
        try:
            tunnels = json.loads(result.stdout)
            for tunnel in tunnels:
                if tunnel.get("name") == tunnel_name:
                    return tunnel.get("id")
        except:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if tunnel_name in line:
                    return line.split()[0]
        
        return None
    
    def add_route(self, domain: str, localhost_port: int, end_user_url: str = "localhost", tunnel_name: str = 'Universal-Tunnel') -> bool:
        """Add a route from domain to localhost:port"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified. Use manager.use_tunnel('name') first")
            return False
        
        if tunnel_name != self.tunnel_name:
            self.use_tunnel(tunnel_name)
        
        if not self.create_tunnel(tunnel_name):
            return False
        
        tunnel_id = self._get_tunnel_id(tunnel_name)
        if not tunnel_id:
            print("Could not find tunnel ID")
            return False
        
        config = self._load_config()
        
        is_first_route = "ingress" not in config or len([r for r in config.get("ingress", []) 
                                                        if isinstance(r, dict) and "hostname" in r]) == 0
        
        if is_first_route and self.auto_start_on_create:
            if not self.check_autostart_status(verbose=False):
                print("\nTip: You're adding your first route.")
                
        
        config["tunnel"] = tunnel_id
        
        # Find credentials file
        creds_file = None
        for file in os.listdir(self.config_dir):
            if file.startswith(tunnel_id) and file.endswith(".json"):
                creds_file = os.path.join(self.config_dir, file)
                break
        
        if creds_file:
            config["credentials-file"] = creds_file
        
        if "ingress" not in config:
            config["ingress"] = []
        
        # Remove existing rule for this domain and catch-all
        config["ingress"] = [r for r in config["ingress"] 
                           if not (isinstance(r, dict) and r.get("hostname") == domain)]
        config["ingress"] = [r for r in config["ingress"] 
                           if r.get("service") != "http_status:404"]
        
        # Add new rule and catch-all
        config["ingress"].append({
            "hostname": domain,
            "service": f"http://{end_user_url}:{localhost_port}"
        })
        config["ingress"].append({"service": "http_status:404"})
        
        self._save_config(config)
        
        # Update state
        state = self._load_state()
        if tunnel_name in state["tunnels"]:
            routes = state["tunnels"][tunnel_name].get("routes", [])
            routes = [r for r in routes if r.get("domain") != domain]
            routes.append({"domain": domain, "service": f"http://{end_user_url}:{localhost_port}"})
            self._update_tunnel_info(tunnel_name, {"routes": routes})
        
        # Create DNS record
        print(f"Creating DNS record for {domain}...")
        try:
            subprocess.run([
                "cloudflared", "tunnel", "route", "dns",
                tunnel_name, domain
            ], check=True)
            print(f"DNS record created for {domain}")
        except subprocess.CalledProcessError:
            print(f"Failed to create DNS record. You may need to create it manually.")
        
        self._restart_tunnel()
        
        print(f"Route added: {domain} -> {end_user_url}:{localhost_port}")
        return True
    
    def _restart_tunnel(self):
        """Restart the tunnel service"""
        if not self.tunnel_name:
            return
            
        # Check if using system service
        if self.check_autostart_status(verbose=False):
            print("Restarting service...")
            
            env = self._detect_environment()
            
            if self._has_systemd_user_support():
                try:
                    subprocess.run(["systemctl", "--user", "restart", f"cloudflared-tunnel-{self.tunnel_name}"], check=True)
                    print("User service restarted")
                    return
                except subprocess.CalledProcessError:
                    print("Failed to restart service, falling back to manual restart")
            
        # Manual restart
        subprocess.run(["pkill", "-f", f"cloudflared.*tunnel.*{self.tunnel_name}"], capture_output=True)
        time.sleep(2)
        
        # Start tunnel in background
        subprocess.Popen(
            ["cloudflared", "tunnel", "--config", self.config_file, "run", self.tunnel_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("Tunnel restarted")
    
    def list_routes(self, tunnel_name: str = None) -> List[Dict]:
        """List all configured routes for a tunnel"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return []
        
        state = self._load_state()
        if tunnel_name in state["tunnels"]:
            return state["tunnels"][tunnel_name].get("routes", [])
        
        old_tunnel = self.tunnel_name
        old_config = self.config_file
        
        self.tunnel_name = tunnel_name
        self.config_file = os.path.join(self.config_dir, f"config-{tunnel_name}.yml")
        
        config = self._load_config()
        routes = []
        
        if "ingress" in config:
            for rule in config["ingress"]:
                if isinstance(rule, dict) and "hostname" in rule:
                    routes.append({
                        "domain": rule["hostname"],
                        "service": rule["service"]
                    })
        
        self.tunnel_name = old_tunnel
        self.config_file = old_config
        
        return routes
    
    def show_routes(self, tunnel_name: str = None):
        """Display all routes in a formatted table"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return
            
        routes = self.list_routes(tunnel_name)
        
        if not routes:
            print(f"No routes configured for tunnel '{tunnel_name}'")
            return
        
        status = self._check_tunnel_status(tunnel_name)
        
        print(f"\n=== Routes for tunnel '{tunnel_name}' ===")
        print(f"Status: {status}")
        print("\n" + "="*60)
        print(f"{'Domain':<35} {'Port':<10}")
        print("="*60)
        
        for route in routes:
            domain = route['domain']
            service = route['service']
            port = service.split(':')[-1] if ':' in service else 'unknown'
            print(f"{domain:<35} {port:<10}")
        
        print("="*60 + "\n")
    
    def status(self, tunnel_name: str = None):
        """Show comprehensive tunnel status"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            self.list_all_tunnels()
            return
        
        env = self._detect_environment()
        print(f"\n=== Cloudflare Tunnel Status: {tunnel_name} ===")
        print(f"Environment: {env}")
        
        tunnel_id = self._get_tunnel_id(tunnel_name)
        if tunnel_id:
            print(f"\nTunnel: {tunnel_name} (ID: {tunnel_id})")
        else:
            print(f"\nTunnel '{tunnel_name}' not found")
            return
        
        print("\nConfigured Routes:")
        routes = self.list_routes(tunnel_name)
        if routes:
            for route in routes:
                port = route['service'].split(':')[-1]
                print(f"  • {route['domain']} -> localhost:{port}")
        else:
            print("  No routes configured")
        
        print("\nAuto-start Configuration:")
        old_tunnel = self.tunnel_name
        self.tunnel_name = tunnel_name
        has_autostart = self.check_autostart_status(verbose=False)
        self.tunnel_name = old_tunnel
        
        if has_autostart:
            print("  Enabled - Tunnel starts automatically")
            
            status = self._check_tunnel_status(tunnel_name)
            if "running" in status:
                print(f"  Service is {status}")
            else:
                print(f"  Service is {status}")
        else:
            print("  Not enabled")
            print(f"    Enable with: manager.use_tunnel('{tunnel_name}') && manager.setup_autostart()")
        
        if not has_autostart:
            result = subprocess.run(
                ["pgrep", "-f", f"cloudflared.*tunnel.*{tunnel_name}"],
                capture_output=True
            )
            if result.returncode == 0:
                print("\n  Tunnel is running manually (not as system service)")
            else:
                print("\n  Tunnel is not running")
                print(f"    Start with: manager.use_tunnel('{tunnel_name}') && manager.start_tunnel()")
        
        print()
    
    def setup_autostart(self) -> bool:
        """Set up tunnel to start automatically on system boot"""
        if not self.tunnel_name:
            print("No tunnel selected. Use manager.use_tunnel('name') first")
            return False
        
        env = self._detect_environment()
        print(f"Environment detected: {env}")
        
        system = sys.platform
        
        if system.startswith('linux'):
            if self._has_systemd_user_support():
                success = self._setup_systemd_user_service()
            else:
                success = self._setup_cron_service()
        elif system == 'darwin':
            success = self._setup_launchagent_service()
        elif system == 'win32':
            success = self._setup_task_scheduler()
        else:
            print(f"Auto-start not supported for platform: {system}")
            success = False
        
        if success:
            self._update_tunnel_info(self.tunnel_name, {"auto_start": True})
        
        return success
    
    def _setup_systemd_user_service(self) -> bool:
        """Set up systemd user service for Linux (no sudo required)"""
        print(f"\n=== Setting up User Systemd Service for '{self.tunnel_name}' ===")
        
        # Create user systemd directory
        service_dir = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(service_dir, exist_ok=True)
        
        # Find cloudflared binary path
        cloudflared_path = None
        for path in ["/usr/local/bin/cloudflared", "/usr/bin/cloudflared", 
                    os.path.expanduser("~/.local/bin/cloudflared")]:
            if os.path.exists(path):
                cloudflared_path = path
                break
        
        if not cloudflared_path:
            # Try to find in PATH
            result = subprocess.run(["which", "cloudflared"], capture_output=True, text=True)
            if result.returncode == 0:
                cloudflared_path = result.stdout.strip()
            else:
                print("cloudflared binary not found in common locations")
                return False
        
        # Create service file content
        service_content = f"""[Unit]
Description=Cloudflare Tunnel - {self.tunnel_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={cloudflared_path} tunnel --config {self.config_file} run {self.tunnel_name}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
        
        service_file = f"{service_dir}/cloudflared-tunnel-{self.tunnel_name}.service"
        
        try:
            # Write service file directly (no sudo needed)
            with open(service_file, 'w') as f:
                f.write(service_content)
            
            os.chmod(service_file, 0o644)
            
            # Reload and enable user service
            print("Reloading user systemd...")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            
            print("Enabling service...")
            subprocess.run(["systemctl", "--user", "enable", f"cloudflared-tunnel-{self.tunnel_name}.service"], check=True)
            
            print("Starting service...")
            subprocess.run(["systemctl", "--user", "start", f"cloudflared-tunnel-{self.tunnel_name}.service"], check=True)
            
            # Enable lingering for user services to start at boot
            try:
                subprocess.run(["loginctl", "enable-linger", os.environ.get('USER')], check=True)
                print("Enabled user service lingering (starts at boot)")
            except:
                print("Note: Service will start when you log in (enable-linger failed)")
            
            print("\nService installed successfully!")
            print("\nUseful commands:")
            print(f"  Check status:  systemctl --user status cloudflared-tunnel-{self.tunnel_name}")
            print(f"  View logs:     journalctl --user -u cloudflared-tunnel-{self.tunnel_name} -f")
            print(f"  Stop service:  systemctl --user stop cloudflared-tunnel-{self.tunnel_name}")
            print(f"  Disable:       systemctl --user disable cloudflared-tunnel-{self.tunnel_name}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\nFailed to install service: {e}")
            return False
    
    def _setup_cron_service(self) -> bool:
        """Set up cron-based auto-start for systems without systemd"""
        print(f"\n=== Setting up Cron Auto-start for '{self.tunnel_name}' ===")
        
        # Find cloudflared binary
        result = subprocess.run(["which", "cloudflared"], capture_output=True, text=True)
        if result.returncode != 0:
            print("cloudflared binary not found")
            return False
        
        cloudflared_path = result.stdout.strip()
        
        # Create startup script
        script_dir = os.path.expanduser("~/.local/bin")
        os.makedirs(script_dir, exist_ok=True)
        
        script_path = f"{script_dir}/start-tunnel-{self.tunnel_name}.sh"
        script_content = f"""#!/bin/bash
# Auto-start script for tunnel {self.tunnel_name}

# Kill any existing instances
pkill -f "cloudflared.*tunnel.*{self.tunnel_name}" 2>/dev/null || true

# Wait a moment
sleep 2

# Start tunnel
cd {os.path.dirname(self.config_file)}
{cloudflared_path} tunnel --config {self.config_file} run {self.tunnel_name} &

echo "Tunnel {self.tunnel_name} started at $(date)"
"""
        
        try:
            with open(script_path, 'w') as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
            
            # Add to crontab
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            current_cron = result.stdout if result.returncode == 0 else ""
            
            cron_line = f"@reboot {script_path}"
            
            if cron_line not in current_cron:
                new_cron = current_cron + f"\n{cron_line}\n"
                
                # Write new crontab
                process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
                process.communicate(input=new_cron)
                
                if process.returncode == 0:
                    print("Cron job added successfully!")
                    print(f"Script created: {script_path}")
                    
                    # Start tunnel now
                    subprocess.run([script_path], check=True)
                    
                    return True
                else:
                    print("Failed to add cron job")
                    return False
            else:
                print("Cron job already exists")
                return True
                
        except Exception as e:
            print(f"Failed to set up cron service: {e}")
            return False
    
    def _setup_launchagent_service(self) -> bool:
        """Set up LaunchAgent for macOS (user-level, no sudo)"""
        print(f"\n=== Setting up LaunchAgent for '{self.tunnel_name}' ===")
        
        # Create user LaunchAgents directory
        agents_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(agents_dir, exist_ok=True)
        
        # Find cloudflared binary
        cloudflared_paths = ["/usr/local/bin/cloudflared", "/opt/homebrew/bin/cloudflared"]
        cloudflared_path = None
        
        for path in cloudflared_paths:
            if os.path.exists(path):
                cloudflared_path = path
                break
        
        if not cloudflared_path:
            result = subprocess.run(["which", "cloudflared"], capture_output=True, text=True)
            if result.returncode == 0:
                cloudflared_path = result.stdout.strip()
            else:
                print("cloudflared binary not found")
                return False
        
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.cloudflare.tunnel.{self.tunnel_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cloudflared_path}</string>
        <string>tunnel</string>
        <string>--config</string>
        <string>{self.config_file}</string>
        <string>run</string>
        <string>{self.tunnel_name}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{os.path.expanduser('~/Library/Logs')}/cloudflared-tunnel-{self.tunnel_name}.log</string>
    <key>StandardErrorPath</key>
    <string>{os.path.expanduser('~/Library/Logs')}/cloudflared-tunnel-{self.tunnel_name}.error.log</string>
</dict>
</plist>"""
        
        plist_file = f"{agents_dir}/com.user.cloudflare.tunnel.{self.tunnel_name}.plist"
        
        try:
            # Create logs directory
            os.makedirs(os.path.expanduser('~/Library/Logs'), exist_ok=True)
            
            # Write plist file (no sudo needed)
            with open(plist_file, 'w') as f:
                f.write(plist_content)
            
            # Load service
            print("Loading LaunchAgent...")
            subprocess.run(["launchctl", "load", plist_file], check=True)
            subprocess.run(["launchctl", "start", f"com.user.cloudflare.tunnel.{self.tunnel_name}"], check=True)
            
            print("\nLaunchAgent installed successfully!")
            print("\nUseful commands:")
            print(f"  Check status:  launchctl list | grep {self.tunnel_name}")
            print(f"  Stop service:  launchctl stop com.user.cloudflare.tunnel.{self.tunnel_name}")
            print(f"  Unload:        launchctl unload {plist_file}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\nFailed to install LaunchAgent: {e}")
            return False
    
    def _setup_task_scheduler(self) -> bool:
        """Set up Windows Task Scheduler (user-level)"""
        print(f"\n=== Setting up Windows Task for '{self.tunnel_name}' ===")
        
        try:
            # Create a batch file for this specific tunnel
            batch_content = f'@echo off\ncloudflared tunnel --config "{self.config_file}" run {self.tunnel_name}'
            batch_file = os.path.join(self.config_dir, f"tunnel-{self.tunnel_name}.bat")
            
            with open(batch_file, 'w') as f:
                f.write(batch_content)
            
            # Create task using schtasks (user-level)
            task_name = f"CloudflaredTunnel-{self.tunnel_name}"
            
            subprocess.run([
                "schtasks", "/create", "/tn", task_name,
                "/tr", f'"{batch_file}"',
                "/sc", "onlogon",
                "/rl", "limited"
            ], check=True)
            
            # Start the task
            subprocess.run(["schtasks", "/run", "/tn", task_name], check=True)
            
            print("\nTask scheduled successfully!")
            print(f"\nUseful commands:")
            print(f"  Check status:  schtasks /query /tn {task_name}")
            print(f"  Stop task:     schtasks /end /tn {task_name}")
            print(f"  Delete:        schtasks /delete /tn {task_name}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\nFailed to create scheduled task: {e}")
            return False
    
    def check_autostart_status(self, verbose: bool = True) -> bool:
        """Check if autostart is enabled for current tunnel"""
        if not self.tunnel_name:
            return False
            
        system = sys.platform
        
        if system.startswith('linux'):
            if self._has_systemd_user_support():
                try:
                    result = subprocess.run(
                        ["systemctl", "--user", "is-enabled", f"cloudflared-tunnel-{self.tunnel_name}"],
                        capture_output=True,
                        text=True
                    )
                    enabled = result.returncode == 0
                    
                    if verbose:
                        result = subprocess.run(
                            ["systemctl", "--user", "is-active", f"cloudflared-tunnel-{self.tunnel_name}"],
                            capture_output=True,
                            text=True
                        )
                        active = result.returncode == 0
                        
                        print(f"User Systemd Service Status:")
                        print(f"  Enabled: {'Yes' if enabled else 'No'}")
                        print(f"  Active:  {'Yes' if active else 'No'}")
                    
                    return enabled
                except:
                    return False
            else:
                # Check cron
                try:
                    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                    if result.returncode == 0:
                        has_cron = f"start-tunnel-{self.tunnel_name}.sh" in result.stdout
                        if verbose:
                            print(f"Cron Status: {'Enabled' if has_cron else 'Not enabled'}")
                        return has_cron
                except:
                    return False
                
        elif system == 'darwin':
            try:
                result = subprocess.run(
                    ["launchctl", "list"],
                    capture_output=True,
                    text=True
                )
                exists = f"com.user.cloudflare.tunnel.{self.tunnel_name}" in result.stdout
                
                if verbose:
                    if exists:
                        print("LaunchAgent Status: Installed")
                    else:
                        print("LaunchAgent Status: Not installed")
                
                return exists
            except:
                return False
                
        elif system == 'win32':
            try:
                task_name = f"CloudflaredTunnel-{self.tunnel_name}"
                result = subprocess.run(
                    ["schtasks", "/query", "/tn", task_name],
                    capture_output=True,
                    text=True
                )
                installed = result.returncode == 0
                
                if verbose and installed:
                    running = "Running" in result.stdout
                    if running:
                        print("Task Scheduler Status: Running")
                    else:
                        print("Task Scheduler Status: Ready")
                elif verbose:
                    print("Task Scheduler Status: Not installed")
                
                return installed
            except:
                return False
        
        return False
    
    def remove_autostart(self) -> bool:
        """Remove autostart configuration for current tunnel"""
        if not self.tunnel_name:
            print("No tunnel selected")
            return False
            
        system = sys.platform
        success = False
        
        if system.startswith('linux'):
            if self._has_systemd_user_support():
                try:
                    print("Removing user systemd service...")
                    subprocess.run(["systemctl", "--user", "stop", f"cloudflared-tunnel-{self.tunnel_name}"], check=True)
                    subprocess.run(["systemctl", "--user", "disable", f"cloudflared-tunnel-{self.tunnel_name}"], check=True)
                    
                    service_file = os.path.expanduser(f"~/.config/systemd/user/cloudflared-tunnel-{self.tunnel_name}.service")
                    if os.path.exists(service_file):
                        os.remove(service_file)
                    
                    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
                    print("User systemd service removed")
                    success = True
                except:
                    print("Failed to remove user systemd service")
            else:
                # Remove from cron
                try:
                    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                    if result.returncode == 0:
                        lines = result.stdout.split('\n')
                        new_lines = [line for line in lines if f"start-tunnel-{self.tunnel_name}.sh" not in line]
                        
                        if len(new_lines) != len(lines):
                            new_cron = '\n'.join(new_lines)
                            process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
                            process.communicate(input=new_cron)
                            
                            if process.returncode == 0:
                                print("Cron job removed")
                                success = True
                    
                    # Remove script file
                    script_path = os.path.expanduser(f"~/.local/bin/start-tunnel-{self.tunnel_name}.sh")
                    if os.path.exists(script_path):
                        os.remove(script_path)
                        
                except:
                    print("Failed to remove cron job")
                
        elif system == 'darwin':
            try:
                print("Removing LaunchAgent...")
                plist_file = os.path.expanduser(f"~/Library/LaunchAgents/com.user.cloudflare.tunnel.{self.tunnel_name}.plist")
                
                subprocess.run(["launchctl", "stop", f"com.user.cloudflare.tunnel.{self.tunnel_name}"], capture_output=True)
                subprocess.run(["launchctl", "unload", plist_file], capture_output=True)
                
                if os.path.exists(plist_file):
                    os.remove(plist_file)
                
                print("LaunchAgent removed")
                success = True
            except:
                print("Failed to remove LaunchAgent")
                
        elif system == 'win32':
            try:
                print("Removing scheduled task...")
                task_name = f"CloudflaredTunnel-{self.tunnel_name}"
                subprocess.run(["schtasks", "/end", "/tn", task_name], capture_output=True)
                subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], check=True)
                print("Scheduled task removed")
                success = True
            except:
                print("Failed to remove scheduled task")
        
        if success:
            self._update_tunnel_info(self.tunnel_name, {"auto_start": False})
        
        return success
    
    def remove_route(self, domain: str, tunnel_name: str = None) -> bool:
        """Remove a route for a domain"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return False
        
        old_tunnel = self.tunnel_name
        old_config = self.config_file
        
        self.tunnel_name = tunnel_name
        self.config_file = os.path.join(self.config_dir, f"config-{tunnel_name}.yml")
        
        config = self._load_config()
        
        if "ingress" not in config:
            print("No routes configured")
            self.tunnel_name = old_tunnel
            self.config_file = old_config
            return False
        
        original_len = len(config["ingress"])
        config["ingress"] = [r for r in config["ingress"] 
                        if not (isinstance(r, dict) and r.get("hostname") == domain)]
        
        if len(config["ingress"]) == original_len:
            print(f"Route for {domain} not found")
            self.tunnel_name = old_tunnel
            self.config_file = old_config
            return False
        
        self._save_config(config)
        
        state = self._load_state()
        if tunnel_name in state["tunnels"]:
            routes = state["tunnels"][tunnel_name].get("routes", [])
            routes = [r for r in routes if r.get("domain") != domain]
            self._update_tunnel_info(tunnel_name, {"routes": routes})
        
        print(f"\nDNS Record Removal for: {domain}")
        print("="*50)
        print("cloudflared CLI cannot delete DNS records.")
        print("Please delete the DNS record manually:")
        print(f"1. Go to https://dash.cloudflare.com")
        print(f"2. Select your domain -> DNS -> Records")
        print(f"3. Find and delete the CNAME record for '{domain}'")
        print("="*50)
        
        self._restart_tunnel()
        
        print(f"Route removed: {domain}")
        
        self.tunnel_name = old_tunnel
        self.config_file = old_config
        
        return True

    def delete_tunnel(self, tunnel_name: str = None, force: bool = False) -> bool:
        """Delete a tunnel and remove system services"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return False
        
       
        old_tunnel = self.tunnel_name
        self.use_tunnel(tunnel_name)
        
        has_autostart = self.check_autostart_status(verbose=False)
        
        print(f"\nStopping tunnel '{tunnel_name}' and cleaning up connections...")
        
        subprocess.run(["pkill", "-f", f"cloudflared.*tunnel.*{tunnel_name}"], capture_output=True)
        
        if has_autostart:
            system = sys.platform
            if system.startswith('linux') and self._has_systemd_user_support():
                subprocess.run(["systemctl", "--user", "stop", f"cloudflared-tunnel-{tunnel_name}"], capture_output=True)
            elif system == 'darwin':
                subprocess.run(["launchctl", "stop", f"com.user.cloudflare.tunnel.{tunnel_name}"], capture_output=True)
            elif system == 'win32':
                subprocess.run(["schtasks", "/end", "/tn", f"CloudflaredTunnel-{tunnel_name}"], capture_output=True)
        
        time.sleep(3)
        
        print("Cleaning up stale connections...")
        try:
            subprocess.run(["cloudflared", "tunnel", "cleanup", tunnel_name], 
                         capture_output=True, check=True)
            print("Stale connections cleaned up")
        except subprocess.CalledProcessError:
            print("No stale connections to clean up")
        
        if has_autostart:
            print("\nRemoving auto-start configuration...")
            self.remove_autostart()
        
        print(f"\nDeleting tunnel '{tunnel_name}'...")
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                subprocess.run([
                    "cloudflared", "tunnel", "delete", tunnel_name
                ], check=True, capture_output=True, text=True)
                print("Tunnel deleted successfully")
                break
            except subprocess.CalledProcessError as e:
                if "active connections" in str(e.stderr):
                    retry_count += 1
                    if retry_count < max_retries:
                        print(f"Active connections still exist. Retrying ({retry_count}/{max_retries})...")
                        time.sleep(5)
                        
                        subprocess.run(["pkill", "-9", "-f", "cloudflared"], capture_output=True)
                        subprocess.run(["cloudflared", "tunnel", "cleanup", tunnel_name], 
                                     capture_output=True)
                    else:
                        print("\nFailed to delete tunnel: Active connections persist")
                        print("\nTry running these commands manually:")
                        print(f"  pkill -9 -f cloudflared")
                        print(f"  cloudflared tunnel cleanup {tunnel_name}")
                        print(f"  cloudflared tunnel delete {tunnel_name}")
                        self.tunnel_name = old_tunnel
                        return False
                else:
                    print(f"Failed to delete tunnel: {e.stderr}")
                    self.tunnel_name = old_tunnel
                    return False
        
        if os.path.exists(self.config_file):
            os.remove(self.config_file)
            print("Configuration file removed")
        
        tunnel_id = self._get_tunnel_id(tunnel_name)
        if tunnel_id:
            for file in os.listdir(self.config_dir):
                if file.startswith(tunnel_id) and file.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.config_dir, file))
                        print(f"Removed credential file: {file}")
                    except:
                        pass
        
        self._unregister_tunnel(tunnel_name)
        
        print(f"\nTunnel '{tunnel_name}' and all associated configurations have been removed")
        print("Note: DNS records need to be removed manually from Cloudflare dashboard")
        
        if old_tunnel == tunnel_name:
            self.tunnel_name = None
            self.config_file = None
        else:
            self.tunnel_name = old_tunnel
            self.config_file = os.path.join(self.config_dir, f"config-{old_tunnel}.yml")
        
        return True
    
    def start_tunnel(self, tunnel_name: str = None):
        """Start a tunnel"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return
        
        config_file = os.path.join(self.config_dir, f"config-{tunnel_name}.yml")
        
        if not os.path.exists(config_file):
            print(f"No configuration found for tunnel '{tunnel_name}'. Add a route first.")
            return
        
        print(f"Starting tunnel '{tunnel_name}'...")
        subprocess.Popen([
            "cloudflared", "tunnel", "--config", config_file, "run", tunnel_name
        ])
        print("Tunnel started")
    
    def stop_tunnel(self, tunnel_name: str = None):
        """Stop a running tunnel"""
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            
        if not tunnel_name:
            print("No tunnel specified")
            return
        
        print(f"Stopping tunnel '{tunnel_name}'...")
        
        state = self._load_state()
        tunnel_info = state["tunnels"].get(tunnel_name, {})
        
        if tunnel_info.get("auto_start"):
            system = sys.platform
            if system.startswith('linux') and self._has_systemd_user_support():
                subprocess.run(["systemctl", "--user", "stop", f"cloudflared-tunnel-{tunnel_name}"], check=True)
            elif system == 'darwin':
                subprocess.run(["launchctl", "stop", f"com.user.cloudflare.tunnel.{tunnel_name}"], check=True)
            elif system == 'win32':
                subprocess.run(["schtasks", "/end", "/tn", f"CloudflaredTunnel-{tunnel_name}"], check=True)
        else:
            subprocess.run(["pkill", "-f", f"cloudflared.*tunnel.*{tunnel_name}"], capture_output=True)
        
        print("Tunnel stopped")
    
    def quick_setup(self, tunnel_name: str = None, enable_autostart: bool = True):
        """Quick setup wizard for a new tunnel"""
        print("\n=== Cloudflare Tunnel Quick Setup ===\n")
        
        env = self._detect_environment()
        print(f"Environment: {env}")
        
        if not os.path.exists(os.path.join(self.config_dir, "cert.pem")):
            print("Step 1: Login to Cloudflare")
            self.login()
        else:
            print("Already logged in to Cloudflare")
        
        
        if not tunnel_name:
            print("Tunnel name required")
            return
    
        print(f"\nStep 2: Setting up tunnel '{tunnel_name}'")
        self.use_tunnel(tunnel_name)
        if not self.create_tunnel(tunnel_name):
            print("Failed to create tunnel")
            return
        
        print("\nStep 3: Current configuration")
        self.show_routes(tunnel_name)
        
        if enable_autostart:
            print("\nStep 4: Setting up auto-start")
            if self.setup_autostart():
                print("Tunnel will start automatically")
            else:
                print("Auto-start setup failed (manual setup may be required)")
        
        print("\nSetup complete!")
        print(f"\nNext steps:")
        print(f"1. Add routes: manager.add_route('subdomain.domain.com', port)")
        print(f"2. Check status: manager.status('{tunnel_name}')")
        if not enable_autostart:
            print(f"3. Enable auto-start: manager.use_tunnel('{tunnel_name}') && manager.setup_autostart()")


