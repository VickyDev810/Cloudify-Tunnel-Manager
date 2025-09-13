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
