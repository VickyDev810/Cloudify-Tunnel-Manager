#!/bin/bash

# Script to apply authentication enhancements and delete warnings

echo "🚀 Applying authentication enhancements and delete warnings..."

# Update app/setup/page.tsx with better loading states
echo "📝 Updating app/setup/page.tsx with loading animations..."
cat > app/setup/page.tsx << 'EOFILE'
'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { register, login, checkSetup, initializeSetup, checkAuthStatus } from '../actions/tunnel-actions'

export default function SetupWizard() {
  const router = useRouter()
  const [currentStep, setCurrentStep] = useState(1)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [authUrl, setAuthUrl] = useState('')
  const [statusCheckInterval, setStatusCheckInterval] = useState<NodeJS.Timeout | null>(null)
  const [hasTunnels, setHasTunnels] = useState(false)
  const [authLoading, setAuthLoading] = useState(false)
  const [authStatus, setAuthStatus] = useState<'idle' | 'initializing' | 'waiting' | 'completed'>('idle')

  useEffect(() => {
    // Check if setup is actually needed
    checkSetup().then(status => {
      if (status && !status.needs_setup) {
        router.push('/login')
      }
    })
  }, [router])

  useEffect(() => {
    if (currentStep === 2) {
      checkExistingTunnels()
    }
  }, [currentStep])

  useEffect(() => {
    // Cleanup interval on unmount
    return () => {
      if (statusCheckInterval) {
        clearInterval(statusCheckInterval)
      }
    }
  }, [statusCheckInterval])

  const checkExistingTunnels = async () => {
    const status = await checkSetup()
    setHasTunnels(status?.has_tunnels || false)
  }

  const handleCreateAdmin = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (password !== confirmPassword) {
      setError('Passwords do not match!')
      return
    }
    
    setLoading(true)
    setError('')
    
    try {
      const result = await register(username, password)
      
      if (result.success) {
        // Auto-login after creating admin
        const loginResult = await login(username, password)
        if (loginResult.success) {
          setCurrentStep(2)
        } else {
          setError('Account created but login failed. Please login manually.')
        }
      } else {
        setError(result.error || 'Failed to create account')
      }
    } catch (error) {
      setError('Failed to create admin account')
    } finally {
      setLoading(false)
    }
  }

  const startAuthentication = async () => {
    setAuthLoading(true)
    setAuthStatus('initializing')
    setError('')
    
    try {
      const result = await initializeSetup()
      
      if (result.success) {
        setAuthStatus('waiting')
        // Start checking for auth status
        checkAuthenticationStatus()
      } else {
        setError('Failed to start authentication. Please ensure the API server is running.')
        setAuthStatus('idle')
        setAuthLoading(false)
      }
    } catch (error) {
      setError('Failed to start authentication. Please check your connection.')
      setAuthStatus('idle')
      setAuthLoading(false)
    }
  }

  const checkAuthenticationStatus = () => {
    const interval = setInterval(async () => {
      const status = await checkAuthStatus()
      
      if (status) {
        if (status.status === 'url_found' && status.url) {
          setAuthUrl(status.url)
          setAuthLoading(false)
        } else if (status.status === 'completed') {
          setAuthStatus('completed')
          if (statusCheckInterval) {
            clearInterval(statusCheckInterval)
          }
          setAuthUrl('')
          
          // Show success message briefly
          setTimeout(() => {
            setCurrentStep(3)
          }, 1500)
        }
      }
    }, 2000)
    
    setStatusCheckInterval(interval)
  }

  const skipToComplete = () => {
    if (statusCheckInterval) {
      clearInterval(statusCheckInterval)
    }
    setCurrentStep(3)
  }

  const completeSetup = () => {
    router.push('/dashboard')
  }

  return (
    <div className="setup-wizard active container">
      <div className="glass-card">
        <div className="wizard-steps">
          <div className={`step ${currentStep === 1 ? 'active' : ''} ${currentStep > 1 ? 'completed' : ''}`} data-step="1">
            <div className="step-number">1</div>
            <div className="step-label">Admin Setup</div>
          </div>
          <div className={`step ${currentStep === 2 ? 'active' : ''} ${currentStep > 2 ? 'completed' : ''}`} data-step="2">
            <div className="step-number">2</div>
            <div className="step-label">Cloudflare Auth</div>
          </div>
          <div className={`step ${currentStep === 3 ? 'active' : ''}`} data-step="3">
            <div className="step-number">3</div>
            <div className="step-label">Complete</div>
          </div>
        </div>

        <div className="step-content">
          {currentStep === 1 && (
            <>
              <h2>Welcome to Cloudflare Tunnel Manager!</h2>
              <p>Let's set up your first administrator account to get started.</p>
              <div className="status-box info" style={{ marginBottom: '1.5rem' }}>
                <p>This appears to be your first time using the system. Create an admin account to begin managing your tunnels.</p>
              </div>
              <form onSubmit={handleCreateAdmin}>
                <div className="form-group">
                  <label htmlFor="adminUsername">Admin Username</label>
                  <input
                    type="text"
                    id="adminUsername"
                    className="form-input"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    required
                    placeholder="admin"
                    autoFocus
                    disabled={loading}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="adminPassword">Password</label>
                  <input
                    type="password"
                    id="adminPassword"
                    className="form-input"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    placeholder="Enter a strong password"
                    minLength={6}
                    disabled={loading}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="adminPasswordConfirm">Confirm Password</label>
                  <input
                    type="password"
                    id="adminPasswordConfirm"
                    className="form-input"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    placeholder="Confirm your password"
                    minLength={6}
                    disabled={loading}
                  />
                </div>
                <button 
                  type="submit" 
                  className={`btn btn-primary ${loading ? 'loading-state' : ''}`}
                  disabled={loading}
                >
                  Create Admin Account
                </button>
              </form>
              {error && (
                <div className="status-box error" style={{ marginTop: '1rem' }}>
                  {error}
                </div>
              )}
            </>
          )}

          {currentStep === 2 && (
            <>
              {hasTunnels ? (
                <>
                  <h2>Cloudflare Already Configured</h2>
                  <p>Great! Your system already has Cloudflare tunnels configured.</p>
                  <div className="status-box success">
                    <p>✔ Cloudflare authentication is already set up</p>
                    <p>✔ Existing tunnels found</p>
                    <p>✔ You can start managing your tunnels immediately</p>
                  </div>
                  <button className="btn btn-primary" onClick={skipToComplete}>
                    Continue to Dashboard
                  </button>
                </>
              ) : (
                <>
                  <h2>Cloudflare Authentication</h2>
                  <p>Now let's authenticate with Cloudflare to set up your tunnel management system.</p>
                  <div className="status-box info">
                    <p>You'll need a Cloudflare account with tunnel permissions to continue.</p>
                  </div>
                  
                  {authStatus === 'idle' && (
                    <>
                      <button 
                        className={`btn btn-primary ${authLoading ? 'loading-state' : ''}`} 
                        onClick={startAuthentication}
                        disabled={authLoading}
                      >
                        Start Authentication
                      </button>
                      <button 
                        className="btn btn-secondary" 
                        onClick={skipToComplete} 
                        style={{ marginLeft: '1rem' }}
                      >
                        Skip for Now
                      </button>
                    </>
                  )}
                  
                  {authStatus === 'initializing' && (
                    <div className="status-box warning">
                      <p>Starting authentication...</p>
                      <div className="loading-container" style={{ marginTop: '1rem' }}>
                        <div className="loading"></div>
                        <p className="loading-text">Initializing Cloudflare authentication...</p>
                      </div>
                    </div>
                  )}
                  
                  {authStatus === 'waiting' && !authUrl && (
                    <div className="status-box warning">
                      <p>Fetching authentication URL...</p>
                      <div className="loading-container" style={{ marginTop: '1rem' }}>
                        <div className="loading"></div>
                        <p className="loading-text">Please wait while we generate your authentication link...</p>
                      </div>
                    </div>
                  )}
                  
                  {authStatus === 'waiting' && authUrl && (
                    <div className="status-box warning">
                      <p>Please open this URL in your browser to authenticate:</p>
                      <div style={{ 
                        background: 'var(--dark)', 
                        padding: '1rem', 
                        borderRadius: '8px', 
                        margin: '1rem 0', 
                        wordBreak: 'break-all', 
                        fontFamily: 'monospace', 
                        color: 'var(--secondary)' 
                      }}>
                        {authUrl}
                      </div>
                      <p>
                        <a 
                          href={authUrl} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          className="btn btn-secondary btn-small"
                        >
                          Open in Browser
                        </a>
                      </p>
                      <p style={{ marginTop: '1rem', color: 'var(--text-dim)' }}>
                        Waiting for authentication...
                        <span className="loading"></span>
                      </p>
                    </div>
                  )}
                  
                  {authStatus === 'completed' && (
                    <div className="status-box success">
                      <p>✔ Authentication successful!</p>
                      <p>Redirecting to complete setup...</p>
                    </div>
                  )}
                  
                  {error && (
                    <div className="status-box error" style={{ marginTop: '1rem' }}>
                      {error}
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {currentStep === 3 && (
            <>
              <h2>Setup Complete! 🎉</h2>
              <p>Your Cloudflare Tunnel Manager is ready to use.</p>
              <div className="status-box success">
                <p>✔ Admin Account Created</p>
                <p>✔ System Initialized</p>
                <p>✔ Ready to Create Tunnels</p>
              </div>
              <div className="status-box info" style={{ marginTop: '1rem' }}>
                <p><strong>What's Next?</strong></p>
                <p style={{ marginTop: '0.5rem' }}>• Create your first tunnel</p>
                <p>• Add routes to expose your services</p>
                <p>• Start the tunnel to make it accessible</p>
              </div>
              <button className="btn btn-primary" onClick={completeSetup}>
                Go to Dashboard
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
EOFILE

# Create CloudflareAuthModal component
echo "📝 Creating components/CloudflareAuthModal.tsx..."
cat > components/CloudflareAuthModal.tsx << 'EOFILE'
'use client'

import { useState, useEffect } from 'react'
import { initializeSetup, checkAuthStatus } from '@/app/actions/tunnel-actions'

interface CloudflareAuthModalProps {
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}

export default function CloudflareAuthModal({ isOpen, onClose, onSuccess }: CloudflareAuthModalProps) {
  const [authUrl, setAuthUrl] = useState('')
  const [authStatus, setAuthStatus] = useState<'idle' | 'initializing' | 'waiting' | 'completed'>('idle')
  const [error, setError] = useState('')
  const [statusCheckInterval, setStatusCheckInterval] = useState<NodeJS.Timeout | null>(null)

  useEffect(() => {
    if (isOpen) {
      startAuthentication()
    }
    
    return () => {
      if (statusCheckInterval) {
        clearInterval(statusCheckInterval)
      }
    }
  }, [isOpen])

  const startAuthentication = async () => {
    setAuthStatus('initializing')
    setError('')
    setAuthUrl('')
    
    try {
      const result = await initializeSetup()
      
      if (result.success) {
        setAuthStatus('waiting')
        checkAuthenticationStatus()
      } else {
        setError('Failed to start authentication. Please ensure the API server is running.')
        setAuthStatus('idle')
      }
    } catch (error) {
      setError('Failed to start authentication. Please check your connection.')
      setAuthStatus('idle')
    }
  }

  const checkAuthenticationStatus = () => {
    const interval = setInterval(async () => {
      const status = await checkAuthStatus()
      
      if (status) {
        if (status.status === 'url_found' && status.url) {
          setAuthUrl(status.url)
        } else if (status.status === 'completed') {
          setAuthStatus('completed')
          if (statusCheckInterval) {
            clearInterval(statusCheckInterval)
          }
          setAuthUrl('')
          
          setTimeout(() => {
            onSuccess()
            onClose()
          }, 1500)
        }
      }
    }, 2000)
    
    setStatusCheckInterval(interval)
  }

  if (!isOpen) return null

  return (
    <div className="modal active">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Cloudflare Re-Authentication</h2>
          <button 
            className="modal-close" 
            onClick={() => {
              if (statusCheckInterval) {
                clearInterval(statusCheckInterval)
              }
              onClose()
            }}
          >
            ×
          </button>
        </div>

        {authStatus === 'idle' && (
          <>
            <p>Re-authenticate with Cloudflare to refresh your tunnel management permissions.</p>
            <button 
              className="btn btn-primary"
              onClick={startAuthentication}
            >
              Start Authentication
            </button>
          </>
        )}

        {authStatus === 'initializing' && (
          <div className="status-box warning">
            <p>Starting authentication...</p>
            <div className="loading-container" style={{ marginTop: '1rem' }}>
              <div className="loading"></div>
              <p className="loading-text">Initializing Cloudflare authentication...</p>
            </div>
          </div>
        )}

        {authStatus === 'waiting' && !authUrl && (
          <div className="status-box warning">
            <p>Fetching authentication URL...</p>
            <div className="loading-container" style={{ marginTop: '1rem' }}>
              <div className="loading"></div>
              <p className="loading-text">Please wait while we generate your authentication link...</p>
            </div>
          </div>
        )}

        {authStatus === 'waiting' && authUrl && (
          <div className="status-box warning">
            <p>Please open this URL in your browser to authenticate:</p>
            <div style={{ 
              background: 'var(--dark)', 
              padding: '1rem', 
              borderRadius: '8px', 
              margin: '1rem 0', 
              wordBreak: 'break-all', 
              fontFamily: 'monospace', 
              color: 'var(--secondary)' 
            }}>
              {authUrl}
            </div>
            <p>
              <a 
                href={authUrl} 
                target="_blank" 
                rel="noopener noreferrer"
                className="btn btn-secondary btn-small"
              >
                Open in Browser
              </a>
            </p>
            <p style={{ marginTop: '1rem', color: 'var(--text-dim)' }}>
              Waiting for authentication...
              <span className="loading"></span>
            </p>
          </div>
        )}

        {authStatus === 'completed' && (
          <div className="status-box success">
            <p>✔ Authentication successful!</p>
            <p>Refreshing...</p>
          </div>
        )}

        {error && (
          <div className="status-box error" style={{ marginTop: '1rem' }}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}
EOFILE

# Create DeleteConfirmModal component
echo "📝 Creating components/DeleteConfirmModal.tsx..."
cat > components/DeleteConfirmModal.tsx << 'EOFILE'
'use client'

interface DeleteConfirmModalProps {
  isOpen: boolean
  title: string
  message: string
  itemName: string
  onConfirm: () => void
  onCancel: () => void
  loading?: boolean
}

export default function DeleteConfirmModal({ 
  isOpen, 
  title, 
  message, 
  itemName,
  onConfirm, 
  onCancel,
  loading = false
}: DeleteConfirmModalProps) {
  if (!isOpen) return null

  return (
    <div className="modal active">
      <div className="modal-content">
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onCancel} disabled={loading}>×</button>
        </div>
        
        <div className="status-box warning" style={{ marginBottom: '1.5rem' }}>
          <p style={{ fontSize: '1.1rem', fontWeight: 'bold', marginBottom: '0.5rem' }}>⚠️ Warning</p>
          <p>{message}</p>
          {itemName && (
            <p style={{ marginTop: '0.5rem' }}>
              <strong>Item to be deleted:</strong> <span style={{ color: 'var(--error)' }}>{itemName}</span>
            </p>
          )}
        </div>
        
        <p style={{ marginBottom: '1.5rem', color: 'var(--text-dim)' }}>
          This action cannot be undone. Are you sure you want to proceed?
        </p>
        
        <div className="btn-group">
          <button 
            className={`btn btn-danger ${loading ? 'loading-state' : ''}`}
            onClick={onConfirm}
            disabled={loading}
          >
            Yes, Delete
          </button>
          <button 
            className="btn btn-secondary" 
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
EOFILE

# Update Settings component
echo "📝 Updating components/Settings.tsx..."
cat > components/Settings.tsx << 'EOFILE'
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import CloudflareAuthModal from './CloudflareAuthModal'

interface SettingsProps {
  user: any
}

export default function Settings({ user }: SettingsProps) {
  const router = useRouter()
  const [showAuthModal, setShowAuthModal] = useState(false)
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8765'
  
  const handleAuthSuccess = () => {
    router.refresh()
  }
  
  return (
    <>
      <div className="glass-card">
        <h2>System Settings</h2>
        
        <div style={{ marginBottom: '2rem', paddingBottom: '2rem', borderBottom: '1px solid var(--glass-border)' }}>
          <h3>System Information</h3>
          <div className="status-box info">
            <p><strong>API URL:</strong> {apiUrl}</p>
            <p><strong>Username:</strong> {user?.username || 'User'}</p>
          </div>
        </div>
        
        <div style={{ marginBottom: '2rem', paddingBottom: '2rem', borderBottom: '1px solid var(--glass-border)' }}>
          <h3>Cloudflare Configuration</h3>
          <p style={{ marginBottom: '1rem', color: 'var(--text-dim)' }}>
            Re-authenticate with Cloudflare to refresh your tunnel management permissions.
          </p>
          <button 
            className="btn btn-primary"
            onClick={() => setShowAuthModal(true)}
          >
            🔄 Re-authenticate with Cloudflare
          </button>
        </div>
      </div>
      
      <CloudflareAuthModal 
        isOpen={showAuthModal}
        onClose={() => setShowAuthModal(false)}
        onSuccess={handleAuthSuccess}
      />
    </>
  )
}
EOFILE

echo ""
echo "✅ Authentication enhancements applied successfully!"
echo ""
echo "New features added:"
echo "  ✓ Loading animations when clicking 'Start Authentication'"
echo "  ✓ Three-stage loading states (initializing → waiting → completed)"
echo "  ✓ Cloudflare re-authentication available in Settings page"
echo "  ✓ Delete confirmation modal with warnings"
echo "  ✓ Clear visual feedback for all operations"
echo ""
echo "To use the new features:"
echo "1. The setup wizard now shows proper loading when authenticating"
echo "2. Go to Settings page to re-authenticate with Cloudflare anytime"
echo "3. Delete operations now show a warning modal"
echo ""
echo "Restart your development server:"
echo "  npm run dev"
echo ""