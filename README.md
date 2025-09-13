# Cloudify 🚇
**Tunnel management with ease**

Cloudify lets you manage tunnel services in one go. You can expose your routes and maintain them with ease. Built upon Cloudflare services, you get robust tunnels with security, all running locally on your platform with no third-party dependencies except for cloudflared.

✨ **Key Features:**
- Local tunnel management with Cloudflare's robust infrastructure
- Load balancing support
- Both CLI and frontend interface
- Cross-platform support (Linux, macOS, Windows, Docker)
- Auto-restart functionality for tunnels

## 📋 Table of Contents

- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
  - [Linux/macOS](#linuxmacos)
  - [Windows](#windows)
  - [Docker](#docker)
- [Initial Setup](#-initial-setup)
- [Quick Start](#quick-tunnel-no-setup-required)
- [Frontend Usage](#-frontend-usage)
- [CLI Usage](#-cli-usage)
  - [Basic Commands](#basic-commands)
  - [Tunnel Management](#tunnel-management)
  - [Route Management](#route-management)
- [Docker Usage](#-docker-usage)
- [Examples](#-examples)
- [Contributing](#-contributing)
- [Support](#-support)
- [License](#-license)

## 📋 Prerequisites 

Before installing Cloudify, ensure you have these setup or you can just install and run a temporary tunnel using [Quick Tunnel](#quick-tunnel-no-setup-required) :

1. **Domain** - A domain name for your tunnels
2. **Cloudflare Account** - With your domain added for full functionality

### Cloudflare Setup

Set up your Cloudflare account and add your domain:
👉 [Cloudflare Domain Setup Guide](https://developers.cloudflare.com/fundamentals/manage-domains/add-site/)

## 🚀 Installation

We've made installation easy across most operating systems and environments.

### Linux/macOS

```bash
curl -O https://github.com/VickyDev810/Cloudify-Tunnel-Manager/blob/main/install_scripts/final.sh
bash final.sh
````

### Windows

Download and install the executable file from the releases section.

### Docker

You can either pull the pre-built image or clone and build it yourself:

```bash
# Pull pre-built image
docker pull vickydev810/cloudify:latest

# Or build from source
git clone https://github.com/VickyDev810/Cloudify-Tunnel-Manager.git
cd Cloudify-Tunnel-Manager

docker build -t cloudify:latest .
```

> **📝 Note:** After installation, restart your terminal session or source your shell configuration:
>
> ```bash
> source ~/.bashrc  # or ~/.zshrc
> ```

## ⚙️ Initial Setup

After successful installation, the `cloudify` CLI tool will be available.

### Quick Tunnel (No Setup Required)

For a quick temporary tunnel without any configuration:

```bash
cloudify -p <port>
```

This automatically starts a temporary tunnel on the specified port.

## 🖥️ Frontend Usage

### Starting the Frontend Service

**Linux/macOS:**

```bash
cloudify serve
```

**Windows:**

```bash
cloudify serve --windows
```

Access the frontend at: `http://localhost:8765`

### Frontend Setup Process

1. **Create Account:** Set up a simple account in the frontend
2. **Install Certificates:** Follow the setup instructions to install certificates
3. **Dashboard Access:** After successful setup, you'll see the dashboard

### Creating Your First Tunnel

1. **Create Tunnel:**

   * Enter a tunnel name
   * Choose auto-start option (automatically runs tunnels when services are available)

2. **Add Routes:**

   * **Subdomain:** `subdomain.domain` (e.g., `next.mydomain.com`)

   * **End Route:** `localhost` (or custom endpoint URL)

   * **Port:** Service port (e.g., `3000`)

   > **Note:** Don't include `http://` or `https://` - it's added automatically

## 💻 CLI Usage

### Basic Commands

View all available commands:

```bash
cloudify
```

Initial setup (required for CLI usage):

```bash
cloudify setup
```

### Tunnel Management

**List all tunnel commands:**

```bash
cloudify tunnel
```

**Create a new tunnel:**

```bash
cloudify tunnel create <tunnel_name>
```

**Delete a tunnel:**

```bash
cloudify tunnel delete <tunnel_name>
```

### Route Management

**Add a route:**

```bash
cloudify route add <domain> <port>
```

**View route options:**

```bash
cloudify route add --help
```

**Remove a route:**

```bash
cloudify route remove <subdomain.domain.com>
```

## 🐳 Docker Usage

### Basic Docker Setup

```bash
docker run -dit -p 8765:8765 --name cloudify vickydev810/cloudify:latest
```

### Docker Network Setup (Recommended)

For accessing services outside the container, create a Docker network:

```bash
# Create network
docker network create my-net

# Run Cloudify with network
docker run -dit -p 8765:8765 --name cloudify --network my-net vickydev810/cloudify:latest

# Run your service container on the same network
docker run -dit --name frontend --network my-net <your-image>
```

Access the UI at `localhost:8765` and configure:

* **End User URL:** `frontend` (container name)
* **Port:** `3000` (or your service port)

## 📚 Examples

### Example 1: Exposing a Frontend Service

**Scenario:** Frontend running on port 3000

**Frontend Method:**

1. Create tunnel with desired name
2. Add route:

   * Subdomain: `app.mydomain.com`
   * End Route: `localhost`
   * Port: `3000`

**CLI Method:**

```bash
cloudify tunnel create my-frontend
cloudify route add app.mydomain.com 3000
```

### Example 2: Docker Multi-Service Setup

```bash
# Create network
docker network create app-network

# Start Cloudify
docker run -dit -p 8765:8765 --name cloudify --network app-network vickydev810/cloudify:latest

# Start frontend service
docker run -dit --name frontend --network app-network -p 3000:3000 my-frontend-image

# Start backend service  
docker run -dit --name backend --network app-network -p 8000:8000 my-backend-image
```

Then configure routes in the UI:

* Frontend: `frontend:3000` → `app.mydomain.com`
* Backend: `backend:8000` → `api.mydomain.com`

## 🤝 Contributing

We welcome contributions! Future plans include:

* More robust system architecture
* Additional Cloudflare service integrations
* Next-level automation features

## 💖 Support

If you find this project helpful, consider supporting its development:

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/vickydev810)

