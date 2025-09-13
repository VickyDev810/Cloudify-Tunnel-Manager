'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { login, register, checkSetup } from '../actions/tunnel-actions'

export default function LoginPage() {
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showRegister, setShowRegister] = useState(false)
  const [confirmPassword, setConfirmPassword] = useState('')

  useEffect(() => {
    // Check if setup is needed on login page load
    checkSetup().then(status => {
      if (status && status.needs_setup) {
        router.push('/setup')
      }
    })
  }, [router])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    
    const result = await login(username, password)
    
    if (result.success) {
      router.push('/dashboard')
    } else {
      setError(result.error || 'Login failed')
      setLoading(false)
    }
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    
    setLoading(true)
    setError('')
    
    const result = await register(username, password)
    
    if (result.success) {
      setShowRegister(false)
      setError('')
      alert('Account created successfully! Please login.')
    } else {
      setError(result.error || 'Registration failed')
    }
    setLoading(false)
  }

  const checkAndShowSetup = async () => {
    const status = await checkSetup()
    if (status && status.has_users) {
      if (confirm('Users already exist. Do you still want to run the setup wizard?')) {
        router.push('/setup')
      }
    } else {
      router.push('/setup')
    }
  }

  return (
    <div className="login-container active">
      <div className="login-box">
        <div className="login-logo">
          <h1>Cloudflare Tunnel</h1>
          <p style={{ color: 'var(--text-dim)' }}>
            {showRegister ? 'Create Account' : 'Manager Login'}
          </p>
        </div>
        
        {!showRegister ? (
          <form onSubmit={handleLogin}>
            <div className="form-group">
              <label htmlFor="username">Username</label>
              <input
                type="text"
                id="username"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                placeholder="admin"
              />
            </div>
            <div className="form-group">
              <label htmlFor="password">Password</label>
              <input
                type="password"
                id="password"
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="Enter your password"
              />
            </div>
            <button 
              type="submit" 
              className={`btn btn-primary ${loading ? 'loading-state' : ''}`}
              style={{ width: '100%' }}
              disabled={loading}
            >
              Login
            </button>
          </form>
        ) : (
          <form onSubmit={handleRegister}>
            <div className="form-group">
              <label htmlFor="reg-username">Username</label>
              <input
                type="text"
                id="reg-username"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                placeholder="Choose a username"
              />
            </div>
            <div className="form-group">
              <label htmlFor="reg-password">Password</label>
              <input
                type="password"
                id="reg-password"
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                placeholder="Choose a password"
              />
            </div>
            <div className="form-group">
              <label htmlFor="confirm-password">Confirm Password</label>
              <input
                type="password"
                id="confirm-password"
                className="form-input"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={6}
                placeholder="Confirm your password"
              />
            </div>
            <button 
              type="submit" 
              className={`btn btn-primary ${loading ? 'loading-state' : ''}`}
              style={{ width: '100%' }}
              disabled={loading}
            >
              Create Account
            </button>
          </form>
        )}
        
        {error && (
          <div className="status-box error" style={{ marginTop: '1rem' }}>
            {error}
          </div>
        )}
        
        <div className="setup-link">
          {!showRegister ? (
            <>
              <a href="#" onClick={(e) => { e.preventDefault(); setShowRegister(true); setError(''); }}>
                Create New Account
              </a>
              {' | '}
              <a href="#" onClick={(e) => { e.preventDefault(); checkAndShowSetup(); }}>
                Run Setup Wizard
              </a>
            </>
          ) : (
            <a href="#" onClick={(e) => { e.preventDefault(); setShowRegister(false); setError(''); }}>
              Back to Login
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
