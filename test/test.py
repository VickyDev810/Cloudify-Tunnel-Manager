from config.manager import UniversalTunnelManager
from typing import List, Dict

# Quick access functions for convenience
def create_manager(tunnel_name: str = None) -> UniversalTunnelManager:
    """Create a tunnel manager instance"""
    return UniversalTunnelManager(tunnel_name)

def quick_temp_tunnel(port: int, subdomain: str = None) -> str:
    """Quick function to create a temporary tunnel"""
    manager = UniversalTunnelManager()
    return manager.create_temp_tunnel(port, subdomain)

def quick_tunnel_setup(tunnel_name: str, domain: str, port: int) -> bool:
    """Quick function to set up a complete tunnel with route"""
    manager = UniversalTunnelManager(tunnel_name)
    
    if not manager.create_tunnel(tunnel_name):
        return False
    
    return manager.add_route(domain, port)

def list_all() -> List[Dict]:
    """Quick function to list all tunnels"""
    manager = UniversalTunnelManager()
    return manager.list_all_tunnels()

def cleanup_all_temp():
    """Quick function to stop all temporary tunnels"""
    manager = UniversalTunnelManager()
    manager.stop_temp_tunnel()


# Example usage patterns:
"""
# Basic usage
manager = UniversalTunnelManager()
manager.login()
manager.create_tunnel("my-app")
manager.add_route("app.mydomain.com", 3000)
manager.setup_autostart()

# Quick temporary tunnel
url = quick_temp_tunnel(8080)
print(f"Temporary tunnel: {url}")

# Quick permanent setup
quick_tunnel_setup("api", "api.mydomain.com", 5000)

# Managing multiple tunnels
manager.use_tunnel("frontend")
manager.add_route("frontend.mydomain.com", 3000)

manager.use_tunnel("backend")
manager.add_route("api.mydomain.com", 5000)

# Status checking
manager.status("frontend")
manager.list_all_tunnels()

# Temporary tunnels
manager.create_temp_tunnel(8080)
manager.list_temp_tunnels()
manager.stop_temp_tunnel()
"""