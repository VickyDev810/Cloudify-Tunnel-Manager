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
