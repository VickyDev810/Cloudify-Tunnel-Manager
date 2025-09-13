#!/usr/bin/env python3

import click
import sys
import os
import threading
import subprocess
import time
import signal
from pathlib import Path

# Add the cloud directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'frontend'))

from config.manager import UniversalTunnelManager
from core.login import run_command_live

# Global variables to track server processes
frontend_process = None
frontend_thread = None
shutdown_event = threading.Event()


@click.group(invoke_without_command=True)
@click.version_option(version="1.0.0")
@click.option('--port', '-p', type=int, help='Create temporary tunnel for localhost:port')
@click.option('--subdomain', help='Custom subdomain for temporary tunnel')
@click.pass_context
def cli(ctx, port, subdomain):
    """CloudFlare Tunnel Management CLI Tool"""
    # If --port is provided and no subcommand, create temp tunnel
    if port and ctx.invoked_subcommand is None:
        manager = UniversalTunnelManager()
        
        click.echo(f"Creating temporary tunnel for localhost:{port}...")
        url = manager.create_temp_tunnel(port, subdomain)
        
        if url:
            click.echo(f"Temporary tunnel active: {url}")
            click.echo("Press Ctrl+C to stop")
            
            try:
                # Keep the CLI running until interrupted
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                click.echo("\nStopping temporary tunnel...")
                manager.stop_temp_tunnel()
        else:
            click.echo("Failed to create temporary tunnel")
            sys.exit(1)
    elif ctx.invoked_subcommand is None:
        # No port provided and no subcommand, show help
        click.echo(ctx.get_help())

@cli.group()
def tunnel():
    """Tunnel management commands"""
    pass


@tunnel.command('create')
@click.argument('name', required=False)
@click.option('--no-autostart', is_flag=True, help='Skip setting up auto-start')
def create_tunnel(name, no_autostart):
    """Create a new tunnel"""
    if not name:
        name = click.prompt("Enter tunnel name")
    
    setup_autostart = not no_autostart
    manager = UniversalTunnelManager(name, auto_start_on_create=setup_autostart)
    
    click.echo(f"Creating tunnel '{name}'...")
    
    if manager.create_tunnel(name, setup_auto_start=setup_autostart):
        click.echo(f"Tunnel '{name}' created successfully!")
        
        if setup_autostart and manager.check_autostart_status(verbose=False):
            click.echo("Auto-start configured")
        elif setup_autostart:
            click.echo("Note: Auto-start setup may require manual configuration")
    else:
        click.echo(f"Failed to create tunnel '{name}'")
        sys.exit(1)


@tunnel.command('list')
def list_tunnels():
    """List all managed tunnels"""
    manager = UniversalTunnelManager()
    manager.list_all_tunnels()


@tunnel.command('status')
@click.argument('name', required=False)
def tunnel_status(name):
    """Show tunnel status"""
    if name:
        manager = UniversalTunnelManager(name)
        manager.status(name)
    else:
        manager = UniversalTunnelManager()
        current = manager.get_current_tunnel()
        if current:
            manager.status(current)
        else:
            click.echo("No tunnel specified and no current tunnel selected")
            manager.list_all_tunnels()


@tunnel.command('start')
@click.argument('name', required=False)
def start_tunnel(name):
    """Start a tunnel"""
    if not name:
        manager = UniversalTunnelManager()
        name = manager.get_current_tunnel()
        if not name:
            click.echo("No tunnel specified and no current tunnel selected")
            return
    
    manager = UniversalTunnelManager(name)
    manager.start_tunnel(name)
    click.echo(f"Started tunnel '{name}'")


@tunnel.command('stop')
@click.argument('name', required=False)
def stop_tunnel(name):
    """Stop a tunnel"""
    if not name:
        manager = UniversalTunnelManager()
        name = manager.get_current_tunnel()
        if not name:
            click.echo("No tunnel specified and no current tunnel selected")
            return
    
    manager = UniversalTunnelManager(name)
    manager.stop_tunnel(name)
    click.echo(f"Stopped tunnel '{name}'")


@tunnel.command('delete')
@click.argument('name', required=False)
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def delete_tunnel(name, force):
    """Delete a tunnel"""
    if not name:
        manager = UniversalTunnelManager()
        name = manager.get_current_tunnel()
        if not name:
            click.echo("No tunnel specified and no current tunnel selected")
            return
    
    manager = UniversalTunnelManager(name)
    
    if manager.delete_tunnel(name, force=force):
        click.echo(f"Tunnel '{name}' deleted successfully")
    else:
        click.echo(f"Failed to delete tunnel '{name}'")
        sys.exit(1)


@tunnel.command('adopt')
@click.argument('name')
def adopt_tunnel(name):
    """Adopt an existing unmanaged tunnel"""
    manager = UniversalTunnelManager()
    
    if manager.adopt_tunnel(name):
        click.echo(f"Successfully adopted tunnel '{name}'")
    else:
        click.echo(f"Failed to adopt tunnel '{name}'")
        sys.exit(1)


@tunnel.command('use')
@click.argument('name')
def use_tunnel(name):
    """Switch to managing a different tunnel"""
    manager = UniversalTunnelManager()
    manager.use_tunnel(name)
    click.echo(f"Now using tunnel: {name}")


@tunnel.command('autostart')
@click.argument('name', required=False)
@click.option('--enable', 'action', flag_value='enable', help='Enable auto-start')
@click.option('--disable', 'action', flag_value='disable', help='Disable auto-start')
@click.option('--status', 'action', flag_value='status', help='Check auto-start status')
def autostart_tunnel(name, action):
    """Manage tunnel auto-start"""
    if not name:
        manager = UniversalTunnelManager()
        name = manager.get_current_tunnel()
        if not name:
            click.echo("No tunnel specified and no current tunnel selected")
            return
    
    manager = UniversalTunnelManager(name)
    
    if action == 'enable':
        if manager.setup_autostart():
            click.echo(f"Auto-start enabled for tunnel '{name}'")
        else:
            click.echo(f"Failed to enable auto-start for tunnel '{name}'")
            sys.exit(1)
    elif action == 'disable':
        if manager.remove_autostart():
            click.echo(f"Auto-start disabled for tunnel '{name}'")
        else:
            click.echo(f"Failed to disable auto-start for tunnel '{name}'")
            sys.exit(1)
    else:
        # Default to status check
        manager.check_autostart_status(verbose=True)


@cli.group()
def route():
    """Route management commands"""
    pass


@route.command('add')
@click.argument('domain')
@click.argument('port', type=int)
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
@click.option('--service', default='localhost', help='Service URL (default: localhost)')
def add_route(domain, port, tunnel, service):
    """Add a route from domain to localhost:port"""
    if not tunnel:
        manager = UniversalTunnelManager()
        tunnel = manager.get_current_tunnel()
        if not tunnel:
            tunnel = click.prompt("Enter tunnel name")
    
    manager = UniversalTunnelManager(tunnel)
    
    if manager.add_route(domain, port, service, tunnel):
        click.echo(f"Route added: {domain} -> {service}:{port}")
    else:
        click.echo(f"Failed to add route")
        sys.exit(1)


@route.command('remove')
@click.argument('domain')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
def remove_route(domain, tunnel):
    """Remove a route for a domain"""
    if not tunnel:
        manager = UniversalTunnelManager()
        tunnel = manager.get_current_tunnel()
        if not tunnel:
            tunnel = click.prompt("Enter tunnel name")
    
    manager = UniversalTunnelManager(tunnel)
    
    if manager.remove_route(domain, tunnel):
        click.echo(f"Route removed: {domain}")
    else:
        click.echo(f"Failed to remove route for {domain}")
        sys.exit(1)


@route.command('list')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
def list_routes(tunnel):
    """List routes for a tunnel"""
    if not tunnel:
        manager = UniversalTunnelManager()
        tunnel = manager.get_current_tunnel()
        if not tunnel:
            click.echo("No tunnel specified and no current tunnel selected")
            return
    
    manager = UniversalTunnelManager(tunnel)
    manager.show_routes(tunnel)




@cli.group()
def temp():
    """Temporary tunnel commands"""
    pass


@temp.command('create')
@click.argument('port', type=int)
@click.option('--subdomain', help='Custom subdomain')
def create_temp(port, subdomain):
    """Create a temporary tunnel for localhost:port"""
    manager = UniversalTunnelManager()
    
    click.echo(f"Creating temporary tunnel for localhost:{port}...")
    url = manager.create_temp_tunnel(port, subdomain)
    
    if url:
        click.echo(f"Temporary tunnel active: {url}")
        click.echo("Press Ctrl+C to stop")
        
        try:
            # Keep the CLI running until interrupted
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\nStopping temporary tunnel...")
            manager.stop_temp_tunnel()
    else:
        click.echo("Failed to create temporary tunnel")
        sys.exit(1)


@temp.command('list')
def list_temp():
    """List active temporary tunnels"""
    manager = UniversalTunnelManager()
    manager.list_temp_tunnels()


@temp.command('stop')
@click.argument('url', required=False)
def stop_temp(url):
    """Stop temporary tunnel(s)"""
    manager = UniversalTunnelManager()
    
    if url:
        manager.stop_temp_tunnel(url)
        click.echo(f"Stopped temporary tunnel: {url}")
    else:
        manager.stop_temp_tunnel()
        click.echo("Stopped all temporary tunnels")


@cli.command()
@click.option('--serve-port', default=8765, help='Port to run the API server (default: 8765)')
@click.option('--windows', is_flag=True, help='Port to run the API server (default: 8765) on windows')
@click.option('--frontend-port', '-f', default=8766, help='Port to run the frontend server (default: 8766)')
@click.option('--host', '-h', default='0.0.0.0', help='Host to bind servers (default: 0.0.0.0)')
@click.option('--api-host', default=None, help='Host to bind API server (overrides --host)')
@click.option('--frontend-host', default='0.0.0.0', help='Host to bind frontend server (default: localhost)')
@click.option('--next-frontend', is_flag=True, help='starting the next js frontend server')
@click.option('--skip-install', is_flag=True, help='Skip installing frontend dependencies')
def serve(serve_port, windows, frontend_port, host, api_host, frontend_host, next_frontend, skip_install):
    """Start the API server and optionally the frontend server"""
    
    global frontend_process, frontend_thread, shutdown_event
    
    # Determine hosts
    actual_api_host = api_host if api_host else host
    
    def start_frontend():
        """Start the Next.js frontend development server"""
        global frontend_process
        if next_frontend:
            # Frontend is always next to this file
            frontend_path = Path(__file__).parent / 'frontend'
            print(frontend_path)
            if not frontend_path.exists():
                click.echo("⚠️  Frontend directory not found")
                return
                
            package_json = frontend_path / 'package.json'
            if not package_json.exists():
                click.echo("⚠️  package.json not found in frontend directory")
                return
        
        # Your frontend startup code here...
            
            click.echo(f"🌐 Starting Next.js development server on http://{frontend_host}:{frontend_port}")
            
            try:
                # Install dependencies if node_modules doesn't exist and not skipping install
                node_modules = frontend_path / 'node_modules'
                if not node_modules.exists() and not skip_install:
                    click.echo("📥 Installing dependencies...")
                    npm_install = subprocess.run(['npm', 'install'], cwd=frontend_path, 
                                               capture_output=True, text=True, timeout=300)
                    if npm_install.returncode != 0:
                        click.echo(f"❌ Failed to install dependencies: {npm_install.stderr}")
                        return
                    click.echo("✅ Dependencies installed")
                
                # Start the dev server with specified hostname and port
                cmd = ['npm', 'run', 'dev', '--', '--hostname', frontend_host, '--port', str(frontend_port)]
                
                # Store the process so we can terminate it later
                frontend_process = subprocess.Popen(cmd, cwd=frontend_path,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                click.echo(f"✅ Frontend development server started on http://{frontend_host}:{frontend_port}")
                
                # Wait for shutdown event or process completion
                while not shutdown_event.is_set() and frontend_process.poll() is None:
                    time.sleep(0.1)
                
                # If shutdown was requested and process is still running, terminate it
                if shutdown_event.is_set() and frontend_process.poll() is None:
                    click.echo("🛑 Stopping Next.js development server...")
                    frontend_process.terminate()
                    try:
                        frontend_process.wait(timeout=5)
                        click.echo("✅ Frontend server stopped gracefully")
                    except subprocess.TimeoutExpired:
                        click.echo("⚠️  Force killing frontend server...")
                        frontend_process.kill()
                        frontend_process.wait()
                        
            except FileNotFoundError:
                click.echo("❌ npm not found. Please install Node.js and npm")
            except Exception as e:
                click.echo(f"❌ Frontend server error: {e}")
    
    def start_api():
        """Start the API server"""
        try:
            # Import here to avoid import issues
            
            sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

            if windows:
                from api.routes_win import app, set_server, set_shutdown_callback
            else:
                from api.routes import app, set_server, set_shutdown_callback
                
            import uvicorn
            
            # Set the shutdown callback to notify frontend
            set_shutdown_callback(lambda: shutdown_event.set())
            
            click.echo(f"🚀 Starting API server on http://{actual_api_host}:{serve_port}")
            server = uvicorn.Server(uvicorn.Config(app, host=actual_api_host, port=serve_port, log_level="info"))
            set_server(server)
            server.run()
        except KeyboardInterrupt:
            click.echo("\n🛑 API server stopped")
        except ImportError as e:
            click.echo(f"❌ Failed to import required modules: {e}")
            click.echo("Make sure uvicorn and fastapi are installed: pip install uvicorn fastapi")
            sys.exit(1)
        finally:
            # Ensure shutdown event is set
            shutdown_event.set()
    
    # Handle shutdown gracefully
    def signal_handler(signum, frame):
        click.echo("\n🛑 Shutting down servers...")
        shutdown_event.set()
        
        # Give processes time to shutdown gracefully
        time.sleep(1)
        
        # Force kill frontend if it's still running
        if frontend_process and frontend_process.poll() is None:
            click.echo("🛑 Terminating frontend server...")
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=3)
                click.echo("✅ Frontend server terminated gracefully")
            except subprocess.TimeoutExpired:
                click.echo("⚠️  Force killing frontend server...")
                frontend_process.kill()
                frontend_process.wait()
        
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start frontend in a separate thread if not disabled
        if next_frontend:
            frontend_thread = threading.Thread(target=start_frontend, daemon=False)
            frontend_thread.start()
            time.sleep(2)  # Give frontend time to start
        
        # Start API server (blocking)
        start_api()
        
        # Wait for frontend thread to complete if it exists
        if frontend_thread and frontend_thread.is_alive():
            frontend_thread.join(timeout=10)
        
    except KeyboardInterrupt:
        click.echo("\n🛑 Servers stopped")
    finally:
        shutdown_event.set()


@cli.command()
def setup():
    """Initial setup: Authorize Cloudflared"""
    click.echo("🔧 Starting initial setup...")
    
    # Run login
    click.echo("🔐 Starting login process...")
    cmd = ["cloudflared", "tunnel", "login"]
    try:
        run_command_live(cmd)
        click.echo("✅ Login completed successfully!")
        
        # Optionally create a universal tunnel
        if click.confirm("Would you like to create a universal tunnel now?"):
            tunnel_name = click.prompt("Enter tunnel name", default="Universal-tunnel")
            manager = UniversalTunnelManager(tunnel_name, auto_start_on_create=True)
            if manager.create_tunnel():
                click.echo(f"✅ Tunnel '{tunnel_name}' created successfully!")
            else:
                click.echo(f"❌ Failed to create tunnel '{tunnel_name}'")
    except Exception as e:
        click.echo(f"❌ Setup failed: {e}")


@cli.command()
@click.argument('tunnel_name', required=False)
def quickstart(tunnel_name):
    """Quick setup wizard for a new tunnel"""
    if not tunnel_name:
        tunnel_name = click.prompt("Enter tunnel name", default="Universal-tunnel")
    
    enable_autostart = click.confirm("Enable auto-start?", default=True)
    
    manager = UniversalTunnelManager(tunnel_name, auto_start_on_create=enable_autostart)
    manager.quick_setup(tunnel_name, enable_autostart)


