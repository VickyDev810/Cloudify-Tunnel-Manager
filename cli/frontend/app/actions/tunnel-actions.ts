'use server'

import { cookies } from 'next/headers'

export async function shutdown() {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')

  if (!token) return { success: false, error: 'Not authenticated' }

  try {
    const response = await fetch(`${API_URL}/shutdown`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token.value}`
      },
      cache: 'no-store'
    })

    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8765'

export async function checkSetup() {
  try {
    const response = await fetch(`${API_URL}/setup/check`, {
      method: 'GET',
      cache: 'no-store'
    })
    
    if (response.ok) {
      return await response.json()
    }
  } catch (error) {
    console.error('Setup check failed:', error)
  }
  return null
}

export async function login(username: string, password: string) {
  try {
    const formData = new URLSearchParams()
    formData.append('username', username)
    formData.append('password', password)
    
    const response = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formData,
      cache: 'no-store'
    })
    
    if (response.ok) {
      const data = await response.json()
      const cookieStore = await cookies()
      cookieStore.set('cf_tunnel_auth_token', data.access_token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'lax',
        maxAge: 60 * 60 * 24 * 7 // 7 days
      })
      
      return { success: true }
    } else {
      return { success: false, error: 'Invalid credentials' }
    }
  } catch (error) {
    console.error('Login error:', error)
    return { success: false, error: 'Login failed' }
  }
}

export async function register(username: string, password: string) {
  try {
    const response = await fetch(`${API_URL}/auth/register`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        username: username,
        password: password
      }),
      cache: 'no-store'
    })
    
    if (response.ok) {
      return { success: true }
    } else {
      const error = await response.json()
      return { success: false, error: error.detail || 'Registration failed' }
    }
  } catch (error) {
    console.error('Registration error:', error)
    return { success: false, error: 'Registration failed' }
  }
}

export async function logout() {
  const cookieStore = await cookies()
  cookieStore.delete('cf_tunnel_auth_token')
  return { success: true }
}

export async function verifyAuth(token: string) {
  try {
    const response = await fetch(`${API_URL}/auth/me`, {
      headers: {
        'Authorization': `Bearer ${token}`
      },
      cache: 'no-store'
    })
    
    return response.ok
  } catch (error) {
    return false
  }
}

export async function getUser(token: string) {
  try {
    const response = await fetch(`${API_URL}/auth/me`, {
      headers: {
        'Authorization': `Bearer ${token}`
      },
      cache: 'no-store'
    })
    
    if (response.ok) {
      return await response.json()
    }
  } catch (error) {
    console.error('Get user error:', error)
  }
  return null
}

export async function getTunnels(token: string) {
  try {
    const response = await fetch(`${API_URL}/tunnels`, {
      headers: {
        'Authorization': `Bearer ${token}`
      },
      cache: 'no-store'
    })
    
    if (response.ok) {
      return await response.json()
    }
  } catch (error) {
    console.error('Get tunnels error:', error)
  }
  return { tunnels: [] }
}

export async function createTunnel(tunnelName: string, autoStart: boolean) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${API_URL}/tunnel/create`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({
        tunnel_name: tunnelName,
        auto_start_on_create: autoStart
      }),
      cache: 'no-store'
    })
    
    if (response.ok) {
      return { success: true }
    }
    return { success: false, error: 'Failed to create tunnel' }
  } catch (error) {
    return { success: false, error: 'Failed to create tunnel' }
  }
}

export async function startTunnel(tunnelName: string) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${API_URL}/tunnel/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({ tunnel_name: tunnelName }),
      cache: 'no-store'
    })
    
    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

export async function stopTunnel(tunnelName: string) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${cAPI_URL}/tunnel/stop`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({ tunnel_name: tunnelName }),
      cache: 'no-store'
    })
    
    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

export async function deleteTunnel(tunnelName: string) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${API_URL}/tunnel/delete`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({
        tunnel_name: tunnelName,
        force: true
      }),
      cache: 'no-store'
    })
    
    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

export async function addRoute(tunnelName: string, domain: string, endUrl: string, port: number) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${API_URL}/tunnel/route/add`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({
        tunnel_name: tunnelName,
        domain: domain,
        end_user_url: endUrl,
        localhost_port: port
      }),
      cache: 'no-store'
    })
    
    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

export async function removeRoute(tunnelName: string, domain: string) {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) return { success: false, error: 'Not authenticated' }
  
  try {
    const response = await fetch(`${API_URL}/tunnel/route/remove`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token.value}`
      },
      body: JSON.stringify({
        tunnel_name: tunnelName,
        domain: domain
      }),
      cache: 'no-store'
    })
    
    return { success: response.ok }
  } catch (error) {
    return { success: false }
  }
}

export async function initializeSetup() {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) {
    return { success: false, error: 'Not authenticated' }
  }
  
  try {
    const response = await fetch(`${API_URL}/initial_setup`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token.value}`
      },
      cache: 'no-store'
    })
    
    if (response.ok) {
      return { success: true }
    } else {
      return { success: false, error: 'Failed to initialize setup' }
    }
  } catch (error) {
    console.error('Initialize setup error:', error)
    return { success: false, error: 'Failed to initialize setup' }
  }
}

export async function checkAuthStatus() {
  const cookieStore = await cookies()
  const token = cookieStore.get('cf_tunnel_auth_token')
  
  if (!token) {
    return null
  }
  
  try {
    const response = await fetch(`${API_URL}/status`, {
      headers: {
        'Authorization': `Bearer ${token.value}`
      },
      cache: 'no-store'
    })
    
    if (response.ok) {
      return await response.json()
    }
  } catch (error) {
    console.error('Check auth status error:', error)
  }
  return null
}
