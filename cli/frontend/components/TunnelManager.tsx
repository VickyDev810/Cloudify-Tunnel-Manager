'use client'

import { useState, useEffect, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { createTunnel, startTunnel, stopTunnel, deleteTunnel, addRoute, removeRoute } from '@/app/actions/tunnel-actions'
import LoadingOverlay from './LoadingOverlay'
import { useNotification } from './NotificationProvider'

interface TunnelManagerProps {
  initialTunnels: any[]
}

export default function TunnelManager({ initialTunnels }: TunnelManagerProps) {
  const router = useRouter()
  const { showNotification } = useNotification()
  const [isPending, startTransition] = useTransition()
  const [tunnels, setTunnels] = useState<any[]>(initialTunnels)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showRouteModal, setShowRouteModal] = useState(false)
  const [selectedTunnel, setSelectedTunnel] = useState('')
  const [tunnelName, setTunnelName] = useState('')
  const [autoStart, setAutoStart] = useState(false)
  const [domain, setDomain] = useState('')
  const [endUrl, setEndUrl] = useState('')
  const [port, setPort] = useState('')
  const [loadingMessage, setLoadingMessage] = useState('')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)

  // Auto-refresh every 30 seconds for status updates
  useEffect(() => {
    const interval = setInterval(() => {
      startTransition(() => {
        router.refresh()
      })
    }, 30000)
    return () => clearInterval(interval)
  }, [router])

  // Update local state when props change
  useEffect(() => {
    setTunnels(initialTunnels)
  }, [initialTunnels])

  const refreshWithDelay = (delay: number = 1000) => {
    setTimeout(() => {
      startTransition(() => {
        router.refresh()
      })
    }, delay)
  }

  const handleCreateTunnel = async (e: React.FormEvent) => {
    e.preventDefault()
    setFormLoading(true)
    setLoadingMessage('Creating tunnel...')
    
    try {
      const result = await createTunnel(tunnelName, autoStart)
      if (result.success) {
        showNotification('Tunnel created successfully!', 'success')
        setShowCreateModal(false)
        setTunnelName('')
        setAutoStart(false)
        refreshWithDelay(500)
      } else {
        showNotification(result.error || 'Failed to create tunnel', 'error')
      }
    } catch (error) {
      showNotification('Failed to create tunnel', 'error')
    } finally {
      setFormLoading(false)
      setLoadingMessage('')
    }
  }

  const handleAddRoute = async (e: React.FormEvent) => {
    e.preventDefault()
    setFormLoading(true)
    setLoadingMessage('Adding route...')
    
    try {
      const result = await addRoute(selectedTunnel, domain, endUrl, parseInt(port))
      if (result.success) {
        showNotification('Route added successfully!', 'success')
        setShowRouteModal(false)
        setDomain('')
        setEndUrl('')
        setPort('')
        refreshWithDelay(500)
      } else {
        showNotification(result.error || 'Failed to add route', 'error')
      }
    } catch (error) {
      showNotification('Failed to add route', 'error')
    } finally {
      setFormLoading(false)
      setLoadingMessage('')
    }
  }

  const handleStartTunnel = async (name: string) => {
    setActionLoading(`start-${name}`)
    setLoadingMessage(`Starting tunnel ${name}...`)
    
    try {
      const result = await startTunnel(name)
      if (result.success) {
        showNotification(`Tunnel ${name} started!`, 'success')
        refreshWithDelay(2000)
      } else {
        showNotification(`Failed to start tunnel ${name}`, 'error')
      }
    } catch (error) {
      showNotification(`Failed to start tunnel ${name}`, 'error')
    } finally {
      setActionLoading(null)
      setLoadingMessage('')
    }
  }

  const handleStopTunnel = async (name: string) => {
    setActionLoading(`stop-${name}`)
    setLoadingMessage(`Stopping tunnel ${name}...`)
    
    try {
      const result = await stopTunnel(name)
      if (result.success) {
        showNotification(`Tunnel ${name} stopped!`, 'success')
        refreshWithDelay(2000)
      } else {
        showNotification(`Failed to stop tunnel ${name}`, 'error')
      }
    } catch (error) {
      showNotification(`Failed to stop tunnel ${name}`, 'error')
    } finally {
      setActionLoading(null)
      setLoadingMessage('')
    }
  }

  const handleDeleteTunnel = async (name: string) => {
    if (!confirm(`Delete tunnel "${name}" and all its routes?`)) return
    
    setActionLoading(`delete-${name}`)
    setLoadingMessage(`Deleting tunnel ${name}...`)
    
    try {
      const result = await deleteTunnel(name)
      if (result.success) {
        showNotification(`Tunnel ${name} deleted!`, 'success')
        refreshWithDelay(500)
      } else {
        showNotification(`Failed to delete tunnel ${name}`, 'error')
      }
    } catch (error) {
      showNotification(`Failed to delete tunnel ${name}`, 'error')
    } finally {
      setActionLoading(null)
      setLoadingMessage('')
    }
  }

  const handleRemoveRoute = async (tunnelName: string, domain: string) => {
    if (!confirm(`Remove route ${domain}?`)) return
    
    setActionLoading(`remove-${tunnelName}-${domain}`)
    setLoadingMessage(`Removing route ${domain}...`)
    
    try {
      const result = await removeRoute(tunnelName, domain)
      if (result.success) {
        showNotification(`Route ${domain} removed!`, 'success')
        refreshWithDelay(500)
      } else {
        showNotification(`Failed to remove route ${domain}`, 'error')
      }
    } catch (error) {
      showNotification(`Failed to remove route ${domain}`, 'error')
    } finally {
      setActionLoading(null)
      setLoadingMessage('')
    }
  }

  return (
    <>
      {loadingMessage && <LoadingOverlay message={loadingMessage} />}
      
      <div className="glass-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <h2>Tunnels Management</h2>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {isPending && (
              <span className="loading-small" style={{ marginRight: '0.5rem' }}></span>
            )}
            <button 
              className="btn btn-secondary btn-small"
              onClick={() => {
                startTransition(() => {
                  router.refresh()
                })
              }}
              disabled={isPending}
            >
              🔄 Refresh
            </button>
            <button className="btn btn-primary btn-small" onClick={() => setShowCreateModal(true)}>
              + New Tunnel
            </button>
          </div>
        </div>
        
        <div className="tunnel-list">
          {tunnels.length === 0 ? (
            <div className="empty-state">
              <p>No tunnels found</p>
              <button className="btn btn-primary btn-small" onClick={() => setShowCreateModal(true)}>
                Create First Tunnel
              </button>
            </div>
          ) : (
            tunnels.map(tunnel => (
              <div key={tunnel.name} className="tunnel-item">
                <div className="tunnel-info">
                  <h3>{tunnel.name}</h3>
                  <div>
                    <span className={`tunnel-status ${tunnel.status?.includes('running') ? 'running' : 'stopped'}`}>
                      <span className={`status-dot ${tunnel.status?.includes('running') ? 'running' : 'stopped'}`}></span>
                      {tunnel.status || 'stopped'}
                    </span>
                    {tunnel.auto_start && <span className="tunnel-status">Auto-start</span>}
                  </div>
                  {tunnel.routes?.length > 0 ? (
                    <div className="route-list">
                      {tunnel.routes.map((route: any) => (
                        <div key={route.domain} className="route-item">
                          <span className="route-domain">{route.domain}</span>
                          <div>
                            <span className="route-port">→ {route.service}</span>
                            <button 
                              className={`btn btn-danger btn-small ${actionLoading === `remove-${tunnel.name}-${route.domain}` ? 'loading-state' : ''}`}
                              style={{ marginLeft: '1rem' }}
                              onClick={() => handleRemoveRoute(tunnel.name, route.domain)}
                              disabled={actionLoading === `remove-${tunnel.name}-${route.domain}`}
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p style={{ color: 'var(--text-dim)', marginTop: '0.5rem' }}>No routes configured</p>
                  )}
                </div>
                <div className="btn-group">
                  <button 
                    className="btn btn-primary btn-small"
                    onClick={() => { setSelectedTunnel(tunnel.name); setShowRouteModal(true) }}
                  >
                    + Route
                  </button>
                  {tunnel.status?.includes('running') ? (
                    <button 
                      className={`btn btn-danger btn-small ${actionLoading === `stop-${tunnel.name}` ? 'loading-state' : ''}`}
                      onClick={() => handleStopTunnel(tunnel.name)}
                      disabled={actionLoading === `stop-${tunnel.name}`}
                    >
                      Stop
                    </button>
                  ) : (
                    <button 
                      className={`btn btn-success btn-small ${actionLoading === `start-${tunnel.name}` ? 'loading-state' : ''}`}
                      onClick={() => handleStartTunnel(tunnel.name)}
                      disabled={actionLoading === `start-${tunnel.name}`}
                    >
                      Start
                    </button>
                  )}
                  <button 
                    className={`btn btn-danger btn-small ${actionLoading === `delete-${tunnel.name}` ? 'loading-state' : ''}`}
                    onClick={() => handleDeleteTunnel(tunnel.name)}
                    disabled={actionLoading === `delete-${tunnel.name}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Create Tunnel Modal */}
      {showCreateModal && (
        <div className="modal active">
          <div className="modal-content">
            <div className="modal-header">
              <h2>Create New Tunnel</h2>
              <button className="modal-close" onClick={() => setShowCreateModal(false)} disabled={formLoading}>×</button>
            </div>
            <form onSubmit={handleCreateTunnel}>
              <div className="form-group">
                <label htmlFor="tunnelName">Tunnel Name</label>
                <input
                  type="text"
                  id="tunnelName"
                  className="form-input"
                  value={tunnelName}
                  onChange={(e) => setTunnelName(e.target.value)}
                  placeholder="my-app-tunnel"
                  required
                  disabled={formLoading}
                />
              </div>
              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={autoStart}
                    onChange={(e) => setAutoStart(e.target.checked)}
                    disabled={formLoading}
                  />
                  {' '}Enable auto-start on system boot
                </label>
              </div>
              <div className="btn-group">
                <button 
                  type="submit" 
                  className={`btn btn-primary ${formLoading ? 'loading-state' : ''}`}
                  disabled={formLoading}
                >
                  Create Tunnel
                </button>
                <button 
                  type="button" 
                  className="btn btn-secondary" 
                  onClick={() => setShowCreateModal(false)}
                  disabled={formLoading}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add Route Modal */}
      {showRouteModal && (
        <div className="modal active">
          <div className="modal-content">
            <div className="modal-header">
              <h2>Add New Route</h2>
              <button className="modal-close" onClick={() => setShowRouteModal(false)} disabled={formLoading}>×</button>
            </div>
            <form onSubmit={handleAddRoute}>
              <div className="form-group">
                <label htmlFor="domain">Domain</label>
                <input
                  type="text"
                  id="domain"
                  className="form-input"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  placeholder="app.example.com"
                  required
                  disabled={formLoading}
                />
              </div>
              <div className="form-group">
                <label htmlFor="endUrl">End User URL</label>
                <input
                  type="text"
                  id="endUrl"
                  className="form-input"
                  value={endUrl}
                  onChange={(e) => setEndUrl(e.target.value)}
                  placeholder="localhost"
                  required
                  disabled={formLoading}
                />
              </div>
              <div className="form-group">
                <label htmlFor="port">Local Port</label>
                <input
                  type="number"
                  id="port"
                  className="form-input"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  placeholder="3000"
                  required
                  disabled={formLoading}
                />
              </div>
              <div className="btn-group">
                <button 
                  type="submit" 
                  className={`btn btn-primary ${formLoading ? 'loading-state' : ''}`}
                  disabled={formLoading}
                >
                  Add Route
                </button>
                <button 
                  type="button" 
                  className="btn btn-secondary" 
                  onClick={() => setShowRouteModal(false)}
                  disabled={formLoading}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}
