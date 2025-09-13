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
